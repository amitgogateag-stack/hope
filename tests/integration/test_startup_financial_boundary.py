"""Direct-SQL adversarial boundary tests using disposable restricted logins."""
from datetime import timedelta
import time
from uuid import uuid4
import pytest
from sqlalchemy import text,event
from sqlalchemy.exc import DBAPIError
from test_startup_transition_postgres import isolated_database,startup_database,seeded,prepare,certified_case,execute,MIGRATIONS
from hope.api import paper_startup as startup

pytestmark=pytest.mark.integration

@pytest.mark.parametrize('phase',['bootstrap_only','claimed_without_job','completed'])
def test_direct_financial_commit_requires_live_same_transaction_execution(prepare,phase):
    value=prepare();case=value['case'];worker=value['worker'];target=uuid4()
    startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    if phase=='completed':execute(value)
    elif phase!='bootstrap_only':
        startup.execution_callbacks(worker,case['verify_execution']())
    # R1 denies worker capital creation earlier than the general financial
    # commit guard. Require the specific database rejection, not any failure.
    with pytest.raises(DBAPIError,match='PAPER_CONTROLLED_PORTFOLIO_PROVISIONING_FORBIDDEN'):
        with worker.begin() as connection:
            connection.execute(text('INSERT INTO public.paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,50,50)'),{'id':target})
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM public.paper_portfolios WHERE portfolio_id=:id'),{'id':target}).scalar_one()==0


@pytest.mark.parametrize('stall',['before_admission','after_sql_before_closing_refresh'])
def test_execution_expiry_rejects_admission_at_intended_boundary(prepare,stall):
    """Real PG, real clock; reproduce the former premature failure explicitly."""
    value=prepare();case=value['case'];worker=value['worker']
    startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    if stall=='before_admission':
        case['execution']['expires_at']=int(time.time())+3
        case['pin_authority']('execution')
    reached=[]
    def delay(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO public.paper_startup_executions '):
            reached.append(True)
            while connection.execute(text('SELECT clock_timestamp()<to_timestamp(:expiry)'),
                    {'expiry':case['execution']['expires_at']}).scalar_one():
                connection.execute(text('SELECT pg_sleep(0.1)'))
    if stall=='before_admission':time.sleep(4)
    else:event.listen(worker,'after_cursor_execute',delay)
    try:
        with pytest.raises(RuntimeError,match='DEPLOYMENT_EVIDENCE_INVALID'):
            startup.execution_callbacks(worker,case['verify_execution']())
    finally:
        if stall!='before_admission':event.remove(worker,'after_cursor_execute',delay)
    assert bool(reached)==(stall=='after_sql_before_closing_refresh')
    with worker.connect() as connection:
        for table in ('paper_startup_executions','paper_startup_financial_transactions','paper_effects'):
            assert connection.execute(text('SELECT count(*) FROM '+table)).scalar_one()==0


def test_financial_commit_after_actual_authority_expiry_rolls_back_certified_effects(prepare):
    """Admission succeeds first. Delay only the real COMMIT, after ALL refreshes.

    Keep the separate admission experiment's three-second limit unchanged.
    Here use the fixture's ordinary signed lifetime, without extending it, so
    setup latency cannot cause an admission failure to masquerade as COMMIT proof.
    """
    value=prepare('FILLED');worker=value['worker'];case=value['case']
    startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    reached=[]
    def expire_at_commit(connection):
        count=connection.execute(text('SELECT count(*) FROM paper_effects WHERE job_run_id=:id'),
            {'id':value['run'].job_run_id}).scalar_one()
        if not count:return  # execution admission is a different transaction
        assert count==5
        row=connection.execute(text('SELECT e.expires_at,w.financial_transaction_id=pg_current_xact_id(),'
            'clock_timestamp()<least(to_timestamp(e.expires_at),to_timestamp(w.witness_expires_at)) FROM paper_startup_executions e '
            'JOIN paper_startup_commit_witnesses w USING(authorization_sha256) WHERE e.job_run_id=:id'),
            {'id':value['run'].job_run_id}).one()
        assert tuple(row[1:])==(True,True)
        reached.append({'effects':count,'expiry':row[0]})
        # Active bounded SQL waits avoid idle-session timeout. Use PostgreSQL's
        # clock, not a monkeypatched authority clock or a modified deadline.
        while connection.execute(text('SELECT clock_timestamp()<to_timestamp(:expiry)'),{'expiry':row[0]}).scalar_one():
            connection.execute(text('SELECT pg_sleep(0.1)'))
    event.listen(worker,'commit',expire_at_commit)
    try:
        with pytest.raises(DBAPIError,match='PAPER_STARTUP_FINANCIAL_COMMIT_UNVERIFIED'):
            execute(value)
    finally:event.remove(worker,'commit',expire_at_commit)
    assert len(reached)==1 and reached[0]['effects']==5
    with worker.connect() as connection:
        for table in ('paper_effects','orders','fills','paper_startup_financial_transactions',
                      'paper_startup_commit_witnesses','paper_startup_completions','paper_certified_outcomes'):
            assert connection.execute(text('SELECT count(*) FROM '+table)).scalar_one()==0


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
    # R1 rejects creation immediately; exercise the older transaction-replay
    # boundary using an already funded portfolio instead of creating capital.
    funded_id = value['data']['configuration']['paper_execution']['portfolio_id']
    with pytest.raises(DBAPIError,match='PAPER_STARTUP_FINANCIAL_TRANSACTION_REPLAY'):
        with value['worker'].begin() as connection:
            connection.execute(text(
                'UPDATE public.paper_portfolios SET cash=cash+1 WHERE portfolio_id=:id'
            ), {'id':funded_id})
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
        with pytest.raises(DBAPIError,match='PAPER_ADMISSION_PRINCIPAL_SUBSTITUTION'):
            with worker.begin() as connection:
                connection.exec_driver_sql('SET LOCAL ROLE '+delegate)
                connection.execute(text('INSERT INTO public.paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,50,50)'),{'id':uuid4()})
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP OWNED BY '+delegate)
            connection.exec_driver_sql('DROP ROLE '+delegate)


def test_predefined_server_program_role_blocks_startup_before_consumption(
        prepare,startup_database):
    """Relation-safe flags do not make a server-program role a safe worker."""
    value=prepare();worker=value['worker'];admin=startup_database['admin']
    role=startup_database['role']
    with admin.begin() as connection:
        connection.exec_driver_sql('GRANT pg_execute_server_program TO '+role)
    try:
        with worker.connect() as connection:
            assert connection.execute(text(
                "SELECT pg_has_role(current_user,'pg_execute_server_program','MEMBER'), "
                "has_database_privilege(current_user,current_database(),'CREATE,TEMPORARY')"
            )).one()==(True,False)
        with pytest.raises(DBAPIError,match='PAPER_STARTUP_FINANCIAL_ROLE_UNSAFE'):
            startup.bootstrap(worker,value['case']['verify_startup'](),MIGRATIONS)
        with worker.connect() as connection:
            assert connection.execute(text(
                'SELECT count(*) FROM public.paper_startup_claims'
            )).scalar_one()==0
            assert connection.execute(text(
                'SELECT count(*) FROM public.paper_startup_bootstraps'
            )).scalar_one()==0
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql('REVOKE pg_execute_server_program FROM '+role)


def test_predefined_role_drift_blocks_execution_claim_before_consumption(
        prepare,startup_database):
    """A safe bootstrap cannot authorize a later capability-escalated worker."""
    value=prepare();worker=value['worker'];admin=startup_database['admin']
    role=startup_database['role']
    startup.bootstrap(worker,value['case']['verify_startup'](),MIGRATIONS)
    with admin.begin() as connection:
        connection.exec_driver_sql('GRANT pg_execute_server_program TO '+role)
    try:
        with pytest.raises(DBAPIError,match='PAPER_STARTUP_FINANCIAL_ROLE_UNSAFE'):
            startup.execution_callbacks(worker,value['case']['verify_execution']())
        with worker.connect() as connection:
            assert connection.execute(text(
                'SELECT count(*) FROM public.paper_startup_claims'
            )).scalar_one()==1
            assert connection.execute(text(
                'SELECT count(*) FROM public.paper_startup_executions'
            )).scalar_one()==0
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql('REVOKE pg_execute_server_program FROM '+role)


def test_relation_ownership_drift_blocks_execution_claim_before_consumption(
        prepare,startup_database):
    """A post-bootstrap table owner must not reach one-use execution authority."""
    value=prepare();worker=value['worker'];admin=startup_database['admin']
    role=startup_database['role']
    startup.bootstrap(worker,value['case']['verify_startup'](),MIGRATIONS)
    with admin.begin() as connection:
        owner=connection.execute(text(
            "SELECT pg_get_userbyid(c.relowner) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname='paper_portfolios'"
        )).scalar_one()
        connection.exec_driver_sql('ALTER TABLE public.paper_portfolios OWNER TO '+role)
    try:
        with worker.connect() as connection:
            assert connection.execute(text(
                "SELECT pg_has_role(current_user,c.relowner,'MEMBER') FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname='paper_portfolios'"
            )).scalar_one() is True
        with pytest.raises(DBAPIError,match='PAPER_STARTUP_FINANCIAL_ROLE_UNSAFE'):
            startup.execution_callbacks(worker,value['case']['verify_execution']())
        with worker.connect() as connection:
            assert connection.execute(text(
                'SELECT count(*) FROM public.paper_startup_executions'
            )).scalar_one()==0
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE public.paper_portfolios OWNER TO '+owner)


def test_trigger_function_ownership_drift_blocks_execution_claim(
        prepare,startup_database):
    """A guard-function owner must not consume controlled execution authority."""
    value=prepare();worker=value['worker'];admin=startup_database['admin']
    role=startup_database['role'];function='public.hope_require_startup_financial_write()'
    startup.bootstrap(worker,value['case']['verify_startup'](),MIGRATIONS)
    with admin.begin() as connection:
        owner=connection.execute(text(
            "SELECT pg_get_userbyid(p.proowner) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname="
            "'hope_require_startup_financial_write'"
        )).scalar_one()
        connection.exec_driver_sql('ALTER FUNCTION '+function+' OWNER TO '+role)
    try:
        with worker.connect() as connection:
            assert connection.execute(text(
                "SELECT pg_has_role(current_user,p.proowner,'MEMBER') FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname="
                "'hope_require_startup_financial_write'"
            )).scalar_one() is True
        with pytest.raises(DBAPIError,match='PAPER_STARTUP_FINANCIAL_ROLE_UNSAFE'):
            startup.execution_callbacks(worker,value['case']['verify_execution']())
        with worker.connect() as connection:
            assert connection.execute(text(
                'SELECT count(*) FROM public.paper_startup_executions'
            )).scalar_one()==0
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql('ALTER FUNCTION '+function+' OWNER TO '+owner)


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
            # Use an already funded portfolio to test the deferred commit
            # guard, not the independent R1 no-new-capital trigger.
            connection.execute(text(
                'UPDATE public.paper_portfolios SET cash=cash+1 WHERE portfolio_id=:id'
            ), {'id':value['data']['configuration']['paper_execution']['portfolio_id']})
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


@pytest.mark.parametrize('table',['paper_admission_principals','paper_admission_approvals'])
def test_worker_cannot_provision_independent_trust_even_with_all_table_insert_grant(startup_database,table):
    worker=startup_database['worker']
    sql=("INSERT INTO paper_admission_principals SELECT principal_oid,principal_name,admission_mode,recorded_at FROM paper_admission_principals"
         if table=='paper_admission_principals' else
         "INSERT INTO paper_admission_approvals(target_table,authorization_sha256,principal_oid,approved_row,valid_until) "
         "SELECT 'paper_startup_claims',repeat('a',64),principal_oid,jsonb_build_object('authorization_sha256',repeat('a',64),'database_principal',principal_name),extract(epoch FROM clock_timestamp())::bigint+10 FROM paper_admission_principals")
    with pytest.raises(DBAPIError,match='PAPER_ADMISSION_TRUSTED_WRITER_REQUIRED'):
        with worker.begin() as connection:connection.exec_driver_sql(sql)


@pytest.mark.parametrize('table',['paper_admission_principals','paper_admission_approvals'])
@pytest.mark.parametrize('operation',['UPDATE','DELETE','TRUNCATE'])
def test_independent_trust_is_append_only_even_for_owner(startup_database,prepare,table,operation):
    value=prepare();startup.bootstrap(value['worker'],value['case']['verify_startup'](),MIGRATIONS)
    sql=(f'UPDATE {table} SET recorded_at=recorded_at' if operation=='UPDATE' else
         f'DELETE FROM {table}' if operation=='DELETE' else f'TRUNCATE {table} CASCADE')
    with pytest.raises(DBAPIError,match='AUDIT_EVENT_IMMUTABLE'):
        with startup_database['admin'].begin() as connection:connection.exec_driver_sql(sql)


def test_missing_independent_approval_blocks_verified_in_process_permit(prepare,monkeypatch):
    """Real signatures alone cannot cause admission through the worker connection."""
    from hope.infrastructure.repositories.paper_handoff import SqlAlchemyPaperHandoffRepository
    value=prepare();case=value['case'];permit=case['verify_startup']()
    # Remove only the explicit TEST external-approval producer; production has
    # none. Keep genuine signature/runtime checks and original repository code.
    monkeypatch.setattr(SqlAlchemyPaperHandoffRepository,'_insert',SqlAlchemyPaperHandoffRepository._insert.__wrapped__)
    with pytest.raises(DBAPIError,match='PAPER_ADMISSION_INDEPENDENT_APPROVAL_REQUIRED'):
        startup.bootstrap(value['worker'],permit,MIGRATIONS)
    with value['worker'].connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_handoff_intents')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_startup_claims')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0


@pytest.mark.parametrize('field',['controller_process_sha256','evidence_sha256','database_sha256',
    'policy_authority_sha256','observed_policy_generation','job_run_id','database_principal'])
def test_exact_independent_approval_cannot_be_borrowed_for_contradictory_rows(prepare,startup_database,field):
    from hope.infrastructure.repositories.paper_startup import SqlAlchemyPaperStartupRepository
    value=prepare();case=value['case'];worker=value['worker']
    startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    permit=case['verify_execution']();token=permit.token
    with worker.connect() as connection:values=SqlAlchemyPaperStartupRepository(connection)._common(permit)
    values.update({key:token[key] for key in ('request_sha256','startup_authorization_sha256','handoff_authorization_sha256',
        'first_cycle_key_sha256','database_sha256','job_run_id','job_key','scheduled_for','issued_at','expires_at','paper_only','live_enabled','max_cycles')})
    values.update(authorizer_key_sha256=permit.authorizer_key_sha256,job_run_id=permit.run.job_run_id,
        scheduled_for=permit.run.scheduled_for,database_principal=startup_database['role'])
    startup_database['approve']('paper_startup_executions',values)
    values[field]=(permit.policy_generation+1 if field=='observed_policy_generation' else uuid4() if field=='job_run_id'
        else 'synthetic_foreign_principal' if field=='database_principal' else '0'*64)
    with pytest.raises(DBAPIError,match='PAPER_ADMISSION_INDEPENDENT_APPROVAL_REQUIRED'):
        with worker.begin() as connection:
            connection.execute(text('INSERT INTO paper_startup_executions ('+','.join(values)+') VALUES ('+
                ','.join(':'+key for key in values)+')'),values)
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_startup_executions')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0


def test_unknown_login_cannot_become_ordinary_financial_writer(startup_database):
    from sqlalchemy import create_engine
    admin=startup_database['admin'];role='hope_unknown_'+uuid4().hex;password=uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO {role}')
        connection.exec_driver_sql(f'GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA public TO {role}')
    unknown=create_engine(admin.url.set(username=role,password=password))
    try:
        with pytest.raises(DBAPIError,match='PAPER_ADMISSION_PRINCIPAL_UNCLASSIFIED'):
            with unknown.begin() as connection:
                connection.execute(text('INSERT INTO paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,50,50)'),{'id':uuid4()})
        with unknown.connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM paper_startup_executions')).scalar_one()==0
    finally:
        unknown.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP OWNED BY '+role)
            connection.exec_driver_sql('DROP ROLE '+role)


def test_r1_controlled_cycle_cannot_provision_unrelated_capital_at_commit(prepare):
    """R1 regression: a certified cycle cannot smuggle unrelated funding.

    Use the existing restricted worker and explicitly modeled independent
    admission fixture; no production approval issuer is invoked.
    """
    value = prepare('FILLED')
    worker = value['worker']
    startup.bootstrap(worker, value['case']['verify_startup'](), MIGRATIONS)
    extra = uuid4()

    def inject_unrelated_portfolio(connection, phase):
        if phase == 'commit':
            connection.execute(text(
                'INSERT INTO public.paper_portfolios(portfolio_id,initial_cash,cash) '
                'VALUES (:id,123456789,123456789)'
            ), {'id': extra})

    with pytest.raises(DBAPIError, match='PAPER_CONTROLLED_PORTFOLIO_PROVISIONING_FORBIDDEN'):
        _run_mutated(value, inject_unrelated_portfolio)

    with worker.connect() as connection:
        assert connection.execute(text(
            'SELECT count(*) FROM public.paper_portfolios WHERE portfolio_id=:id'
        ), {'id': extra}).scalar_one() == 0
        assert connection.execute(text(
            'SELECT count(*) FROM public.paper_effects WHERE job_run_id=:id'
        ), {'id': value['run'].job_run_id}).scalar_one() == 0
        assert connection.execute(text(
            'SELECT count(*) FROM public.paper_startup_financial_transactions'
        )).scalar_one() == 0



@pytest.mark.parametrize('attack,denial', [
    ('existing_portfolio_upsert', 'PAPER_CONTROLLED_PORTFOLIO_PROVISIONING_FORBIDDEN'),
    ('initial_capital_update', 'PAPER_CONTROLLED_INITIAL_CAPITAL_MUTATION_FORBIDDEN'),
])
def test_r1_certified_cycle_rejects_capital_rewrite_and_rolls_back(prepare, attack, denial):
    """A valid certified execution cannot use its COMMIT callback to fund itself.

    The portfolio is independently pre-funded; the worker's successful trading
    authority must not permit changes to initial capital. An UPSERT must not
    sidestep the INSERT guard by targeting an already existing portfolio.
    """
    value = prepare('FILLED')
    worker = value['worker']
    startup.bootstrap(worker, value['case']['verify_startup'](), MIGRATIONS)
    funded_id = value['data']['configuration']['paper_execution']['portfolio_id']
    with worker.connect() as connection:
        baseline = connection.execute(text(
            'SELECT initial_cash,cash,version FROM public.paper_portfolios '
            'WHERE portfolio_id=:id'
        ), {'id': funded_id}).one()
    reached = []

    def attack_during_commit(connection, phase):
        if phase != 'commit':
            return
        reached.append(phase)
        if attack == 'existing_portfolio_upsert':
            connection.execute(text(
                'INSERT INTO public.paper_portfolios(portfolio_id,initial_cash,cash) '
                'VALUES (:id,123456789,123456789) '
                'ON CONFLICT (portfolio_id) DO UPDATE '
                'SET initial_cash=EXCLUDED.initial_cash'
            ), {'id': funded_id})
        else:
            connection.execute(text(
                'UPDATE public.paper_portfolios SET initial_cash=initial_cash+1 '
                'WHERE portfolio_id=:id'
            ), {'id': funded_id})

    with pytest.raises(DBAPIError, match=denial):
        _run_mutated(value, attack_during_commit)
    assert reached == ['commit']

    with worker.connect() as connection:
        assert connection.execute(text(
            'SELECT initial_cash,cash,version FROM public.paper_portfolios '
            'WHERE portfolio_id=:id'
        ), {'id': funded_id}).one() == baseline
        assert connection.execute(text(
            'SELECT count(*) FROM public.paper_effects WHERE job_run_id=:id'
        ), {'id': value['run'].job_run_id}).scalar_one() == 0
        assert connection.execute(text(
            'SELECT count(*) FROM public.paper_startup_financial_transactions'
        )).scalar_one() == 0
        assert connection.execute(text(
            'SELECT count(*) FROM public.paper_portfolio_fill_applications '
            'WHERE portfolio_id=:id'
        ), {'id': funded_id}).scalar_one() == 0


def test_r1_portfolio_provisioning_guard_is_enabled(startup_database):
    with startup_database['worker'].connect() as connection:
        rows = connection.execute(text(
            "SELECT tgname,tgenabled FROM pg_catalog.pg_trigger "
            "WHERE tgrelid='public.paper_portfolios'::regclass "
            "AND tgname='aaaa_reject_controlled_portfolio_provisioning'"
        )).all()
    assert rows == [('aaaa_reject_controlled_portfolio_provisioning', 'O')]


def test_r1_controlled_worker_cannot_change_independently_funded_initial_cash(prepare):
    """An existing portfolio does not grant trading workers funding authority."""
    value = prepare('NO_TRADE')
    worker = value['worker']
    startup.bootstrap(worker, value['case']['verify_startup'](), MIGRATIONS)
    funded = value['data']['configuration']['paper_execution']
    with worker.connect() as connection:
        original = connection.execute(text(
            'SELECT initial_cash,cash FROM public.paper_portfolios WHERE portfolio_id=:id'
        ), {'id':funded['portfolio_id']}).one()
    with pytest.raises(DBAPIError, match='PAPER_CONTROLLED_INITIAL_CAPITAL_MUTATION_FORBIDDEN'):
        with worker.begin() as connection:
            connection.execute(text(
                'UPDATE public.paper_portfolios SET initial_cash=initial_cash+1 '
                'WHERE portfolio_id=:id'
            ), {'id':funded['portfolio_id']})
    with worker.connect() as connection:
        assert connection.execute(text(
            'SELECT initial_cash,cash FROM public.paper_portfolios WHERE portfolio_id=:id'
        ), {'id':funded['portfolio_id']}).one() == original
        assert connection.execute(text('SELECT count(*) FROM public.paper_effects')).scalar_one() == 0
