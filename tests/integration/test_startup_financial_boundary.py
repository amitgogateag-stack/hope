"""Direct-SQL adversarial boundary tests using disposable restricted logins."""
from datetime import timedelta
import time
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_startup_transition_postgres import isolated_database,startup_database,seeded,prepare,certified_case,execute,MIGRATIONS
from hope.api import paper_startup as startup

pytestmark=pytest.mark.integration

@pytest.mark.parametrize('phase',['bootstrap_only','claimed_without_job','completed','expired'])
def test_direct_financial_commit_requires_live_same_transaction_execution(prepare,phase):
    value=prepare();case=value['case'];worker=value['worker'];target=uuid4()
    startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    if phase=='completed':execute(value)
    elif phase!='bootstrap_only':
        if phase=='expired':
            case['execution']['expires_at']=int(time.time())+3;case['pin_authority']('execution')
        startup.execution_callbacks(worker,case['verify_execution']())
        if phase=='expired':time.sleep(4)
    with pytest.raises(DBAPIError,match='PAPER_STARTUP_'):
        with worker.begin() as connection:
            connection.execute(text('INSERT INTO public.paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,50,50)'),{'id':target})
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM public.paper_portfolios WHERE portfolio_id=:id'),{'id':target}).scalar_one()==0


FINANCIAL_TABLES={'signals','orders','fills','paper_effects','paper_portfolios','paper_portfolio_positions',
    'paper_portfolio_fill_applications','paper_portfolio_pnl_events','paper_risk_assessments',
    'paper_order_terminal_events','paper_no_trade_decisions','paper_strategy_authorizations','paper_certified_outcomes'}

def test_every_financial_relation_has_enabled_native_write_and_truncate_guards(startup_database):
    with startup_database['worker'].connect() as connection:
        rows=connection.execute(text("SELECT c.relname,t.tgname,t.tgenabled FROM pg_catalog.pg_trigger t "
            "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND t.tgname IN ('trg_startup_financial_write','trg_startup_financial_truncate','zzzz_startup_financial_commit')")).all()
    assert set(rows)=={(table,name,'O') for table in FINANCIAL_TABLES for name in ('trg_startup_financial_write','trg_startup_financial_truncate','zzzz_startup_financial_commit')}


def test_genuine_certified_cycle_binds_financial_rows_and_witness_to_one_transaction(prepare):
    value=prepare('FILLED');startup.bootstrap(value['worker'],value['case']['verify_startup'](),MIGRATIONS)
    execute(value)
    with value['worker'].connect() as connection:
        row=connection.execute(text('SELECT t.transaction_id=w.financial_transaction_id,t.database_principal=current_user '
            'FROM public.paper_startup_financial_transactions t JOIN public.paper_startup_commit_witnesses w USING(authorization_sha256)')).one()
        assert tuple(row)==(True,True)
        assert connection.execute(text('SELECT count(*) FROM paper_effects WHERE job_run_id=:id'),{'id':value['run'].job_run_id}).scalar_one()==5


def _run_mutated(value,mutate):
    from hope.infrastructure import paper_service as service
    permit=value['case']['verify_execution']();check,transaction_check,complete=startup.execution_callbacks(value['worker'],permit)
    def checked(connection,phase):
        transaction_check(connection,phase);mutate(connection,phase)
    return service.run_service_cycle(value['worker'],lambda *_:value['plan'],migrations_dir=MIGRATIONS,
        now=lambda:value['data']['as_of']+timedelta(minutes=1),require_certified=True,execution_check=check,
        expected_cycle_key=service.logical_cycle_key(value['run']),transaction_check=checked,require_new_cycle=True,
        successful_run_observer=complete)


@pytest.mark.parametrize('phase',['prepare','commit'])
def test_set_constraints_cannot_consume_financial_expiry_validation_early(prepare,phase):
    value=prepare('FILLED');startup.bootstrap(value['worker'],value['case']['verify_startup'](),MIGRATIONS)
    def force(connection,current):
        if current==phase:connection.exec_driver_sql('SET CONSTRAINTS ALL IMMEDIATE')
    with pytest.raises(DBAPIError,match='PAPER_STARTUP_FINANCIAL_COMMIT_CONTEXT_UNVERIFIED'):_run_mutated(value,force)
    with value['worker'].connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_startup_financial_transactions')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_startup_executions')).scalar_one()==1


def test_later_transaction_cannot_reuse_a_committed_financial_witness(prepare):
    value=prepare('FILLED');startup.bootstrap(value['worker'],value['case']['verify_startup'](),MIGRATIONS)
    def lost_completion(run):raise RuntimeError('synthetic crash after financial commit')
    with pytest.raises(RuntimeError,match='synthetic crash'):execute(value,complete_override=lost_completion)
    with pytest.raises(DBAPIError,match='PAPER_STARTUP_FINANCIAL_TRANSACTION_REPLAY'):
        with value['worker'].begin() as connection:
            connection.execute(text('INSERT INTO paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,50,50)'),{'id':uuid4()})
    with value['worker'].connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==5
        assert connection.execute(text('SELECT count(*) FROM paper_startup_financial_transactions')).scalar_one()==1
        assert connection.execute(text('SELECT count(*) FROM paper_startup_completions')).scalar_one()==0


@pytest.mark.parametrize('sql',['ALTER TABLE public.paper_effects DISABLE TRIGGER USER',"SET session_replication_role='replica'"])
def test_restricted_login_cannot_disable_database_guards(startup_database,sql):
    with pytest.raises(DBAPIError):
        with startup_database['worker'].begin() as connection:connection.exec_driver_sql(sql)


def test_financial_transaction_ledger_is_immutable_even_to_owner(prepare,startup_database):
    value=prepare();startup.bootstrap(value['worker'],value['case']['verify_startup'](),MIGRATIONS);execute(value)
    for sql in ('UPDATE paper_startup_financial_transactions SET transaction_id=transaction_id',
                'DELETE FROM paper_startup_financial_transactions','TRUNCATE paper_startup_financial_transactions CASCADE'):
        with pytest.raises(DBAPIError,match='AUDIT_EVENT_IMMUTABLE'):
            with startup_database['admin'].begin() as connection:connection.exec_driver_sql(sql)


def test_database_admission_rejects_wrong_scope_even_with_worker_sql(prepare):
    """A valid signature for the wrong role/scope cannot become DB authority."""
    value=prepare();case=value['case'];worker=value['worker']
    startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    valid=case['verify_execution']()
    case['execution']['scope']='BOOTSTRAP_ONLY'
    forged=case['pin_authority']('execution')
    with pytest.raises(RuntimeError):case['verify_execution']()
    token=case['execution']
    columns=('request_sha256','startup_authorization_sha256','handoff_authorization_sha256','first_cycle_key_sha256',
        'database_sha256','job_run_id','job_key','scheduled_for','issued_at','expires_at','paper_only','live_enabled','max_cycles')
    values={key:token[key] for key in columns}
    values.update(authorization_sha256=forged,evidence_sha256=valid.evidence_sha256,
        controller_process_sha256=valid.handoff.token['controller_process_sha256'],authorizer_key_sha256=valid.authorizer_key_sha256,
        policy_authority_sha256=valid.policy_authority_sha256,observed_policy_generation=valid.policy_generation,
        witness_expires_at=valid.expires_at,job_run_id=valid.run.job_run_id,scheduled_for=valid.run.scheduled_for)
    with pytest.raises(DBAPIError,match='PAPER_ADMISSION_'):
        with worker.begin() as connection:
            connection.execute(text('INSERT INTO public.paper_startup_executions ('+','.join(values)+') VALUES ('+
                ','.join(':'+key for key in values)+')'),values)
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_startup_executions')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0




def test_set_role_cannot_drop_the_consumed_login_scope(prepare,startup_database):
    value=prepare();worker=value['worker'];admin=startup_database['admin'];delegate='hope_delegate_'+uuid4().hex
    startup.bootstrap(worker,value['case']['verify_startup'](),MIGRATIONS)
    with admin.begin() as connection:
        connection.exec_driver_sql('CREATE ROLE '+delegate+' NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
        connection.exec_driver_sql('GRANT USAGE ON SCHEMA public TO '+delegate)
        connection.exec_driver_sql('GRANT SELECT ON ALL TABLES IN SCHEMA public TO '+delegate)
        connection.exec_driver_sql('GRANT INSERT ON public.paper_portfolios TO '+delegate)
        connection.exec_driver_sql('GRANT '+delegate+' TO '+startup_database['role'])
    try:
        with pytest.raises(DBAPIError,match='PAPER_STARTUP_'):
            with worker.begin() as connection:
                connection.exec_driver_sql('SET LOCAL ROLE '+delegate)
                connection.execute(text('INSERT INTO public.paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,50,50)'),{'id':uuid4()})
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP OWNED BY '+delegate)
            connection.exec_driver_sql('DROP ROLE '+delegate)


def test_failed_job_cannot_commit_partial_financial_work(prepare):
    from hope.application.jobs import JobRunStatus,create_job_run_completion
    from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
    value=prepare();worker=value['worker'];startup.bootstrap(worker,value['case']['verify_startup'](),MIGRATIONS)
    startup.execution_callbacks(worker,value['case']['verify_execution']())
    with pytest.raises(DBAPIError,match='PAPER_STARTUP_FINANCIAL_COMMIT_UNVERIFIED'):
        with worker.begin() as connection:
            jobs=SqlAlchemyJobRunRepository(connection);assert jobs.claim(value['run'])
            assert jobs.complete(create_job_run_completion(value['run'],JobRunStatus.FAILED,
                value['run'].scheduled_for+timedelta(minutes=1),failure_code='SYNTHETIC_SECURITY_FAILURE'))
            connection.execute(text('INSERT INTO public.paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,50,50)'),{'id':uuid4()})
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM job_runs WHERE job_run_id=:id'),{'id':value['run'].job_run_id}).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_startup_financial_transactions')).scalar_one()==0



def test_unadmitted_login_cannot_create_economic_state(startup_database):
    worker=startup_database['worker'];target=uuid4()
    with pytest.raises(DBAPIError,match='PAPER_ADMISSION_'):
        with worker.begin() as connection:
            connection.execute(text('INSERT INTO paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,50,50)'),{'id':target})
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id'),{'id':target}).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_startup_claims')).scalar_one()==0




@pytest.mark.parametrize('primary_failure',[False,True])
def test_dead_scheduler_backend_preserves_primary_failure_and_never_reports_success(startup_database,primary_failure):
    from hope.infrastructure.scheduling.paper import _operational_paper_scheduler_lock,_PAPER_SCHEDULER_LOCK_NAME
    worker=startup_database['worker'];admin=startup_database['admin'];original=ValueError('SYNTHETIC_FINANCIAL_FAILURE')
    expected=ValueError if primary_failure else DBAPIError
    with pytest.raises(expected) as caught:
        with _operational_paper_scheduler_lock(worker) as lease:
            pid=lease.execute(text('SELECT pg_backend_pid()')).scalar_one()
            with admin.begin() as connection:
                assert connection.execute(text('SELECT pg_terminate_backend(:pid)'),{'pid':pid}).scalar_one() is True
            if primary_failure:raise original
    if primary_failure:assert caught.value is original
    with worker.connect() as connection:
        assert connection.execute(text('SELECT pg_try_advisory_xact_lock(hashtext(:name)::bigint)'),{'name':_PAPER_SCHEDULER_LOCK_NAME}).scalar_one() is True
