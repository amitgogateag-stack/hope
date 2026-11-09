"""Integrated dual-authority PAPER path in disposable DBs with a real limited login.

Only signed runtime/stage/host observations are explicitly modeled. Signatures,
permissions, PG clocks, claims, financial effects and certified recovery are real.
"""
from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
import re
from uuid import uuid4

import pytest
from sqlalchemy import create_engine,text,event
from sqlalchemy.exc import DBAPIError,IntegrityError

from test_migration_upgrade_rehearsal import isolated_database
from test_certified_paper_bridge import certified_case,_constrained_service_plan
from startup_fixture import startup_case
from first_cycle_fixture import SHA,RECEIPT,FACTORY
from hope.api import paper_startup as startup,startup_authority as authority
from hope.infrastructure import paper_service as service
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.postgres.schema_verification import schema_observation
from hope.infrastructure.repositories.paper_handoff import database_identity
from hope.infrastructure.repositories.paper_startup import SqlAlchemyPaperStartupRepository
from hope.domain.strategy.candidates import StrategyMarket

pytestmark=pytest.mark.integration
MIGRATIONS=Path(__file__).parents[2]/'migrations'


@pytest.fixture
def startup_database(isolated_database,monkeypatch):
    admin=isolated_database;role='hope_startup_'+uuid4().hex;password=uuid4().hex
    assert re.fullmatch('hope_startup_[0-9a-f]{32}',role)
    with admin.begin() as connection:
        apply_migrations(connection,MIGRATIONS)
        database_name=connection.dialect.identifier_preparer.quote(admin.url.database)
        connection.exec_driver_sql(f'REVOKE TEMPORARY ON DATABASE {database_name} FROM PUBLIC')
        connection.exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO {role}')
        connection.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO {role}')
        connection.exec_driver_sql(f'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}')
        connection.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO {role}')
    worker=create_engine(admin.url.set(username=role,password=password),connect_args={'options':'-c statement_timeout=10000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=5000'})
    monkeypatch.setenv('HOPE_DATABASE_URL',admin.url.render_as_string(hide_password=False))
    try:yield dict(admin=admin,worker=worker,role=role)
    finally:
        worker.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP OWNED BY '+role)
            connection.exec_driver_sql('DROP ROLE '+role)


@pytest.fixture
def seeded(startup_database,request):return request.getfixturevalue('certified_case')


@pytest.fixture
def prepare(startup_database,seeded,tmp_path,monkeypatch):
    _,make=seeded;worker=startup_database['worker']
    def build(path='NO_TRADE',market=StrategyMarket.USA,*,receipt=RECEIPT):
        data,_,strategy,work,_=make(emit=path!='NO_TRADE',blocked=path=='RISK_BLOCKED',market=market)
        run,plan=_constrained_service_plan(data,work);strategy.run=run
        with worker.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY');identity=database_identity(connection)
            catalog=schema_observation(connection)['sha256']
        folder=tmp_path/uuid4().hex;folder.mkdir()
        case=startup_case(folder,monkeypatch,run,database_sha256=identity,catalog_sha256=catalog,receipt=receipt)
        return dict(case=case,worker=worker,data=data,run=run,plan=plan,strategy=strategy)
    return build


def execute(value,*,check_override=None,transaction_override=None,complete_override=None):
    case=value['case'];worker=value['worker'];permit=case['verify_execution']()
    check,transaction_check,complete=startup.execution_callbacks(worker,permit)
    result=service.run_service_cycle(worker,lambda *_:value['plan'],migrations_dir=MIGRATIONS,
        now=lambda:value['data']['as_of']+timedelta(minutes=1),require_certified=True,execution_check=check_override or check,
        expected_cycle_key=service.logical_cycle_key(value['run']),transaction_check=transaction_override or transaction_check,
        require_new_cycle=True,successful_run_observer=complete_override or complete)
    return result,permit


def observe(value):
    with value['worker'].connect() as connection:
        return SqlAlchemyPaperStartupRepository(connection).observation(value['case']['verify_startup']().authorization_sha256)


@pytest.mark.parametrize('market',[StrategyMarket.USA,StrategyMarket.INDIA])
@pytest.mark.parametrize('path',['NO_TRADE','FILLED','RISK_BLOCKED'])
def test_fresh_dual_authority_startup_executes_one_real_certified_cycle_without_current(prepare,market,path):
    value=prepare(path,market);case=value['case'];worker=value['worker'];permit=case['verify_startup']()
    startup.bootstrap(worker,permit,MIGRATIONS)
    assert observe(value)['state']=='BOOTSTRAPPED'
    with worker.connect() as connection:assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0
    result,execution=execute(value)
    assert result==(1,0) and observe(value)['state']=='CYCLE_COMPLETED'
    assert observe(value)['execution_authorized'] is False and len(value['strategy'].calls)==1
    with worker.connect() as connection:
        assert connection.execute(text('SELECT outcome FROM paper_certified_outcomes WHERE job_run_id=:id'),{'id':value['run'].job_run_id}).scalar_one()==path
        assert connection.execute(text('SELECT count(*) FROM paper_startup_commit_witnesses')).scalar_one()==1
        assert connection.execute(text('SELECT count(*) FROM paper_startup_completions')).scalar_one()==1
    with pytest.raises(IntegrityError):startup.bootstrap(worker,permit,MIGRATIONS)
    with pytest.raises(IntegrityError):startup.execution_callbacks(worker,execution)
    assert len(value['strategy'].calls)==1


def test_bootstrap_authority_never_substitutes_for_execution_and_cannot_be_reused(prepare):
    value=prepare();permit=value['case']['verify_startup']();worker=value['worker']
    startup.bootstrap(worker,permit,MIGRATIONS)
    with pytest.raises(RuntimeError):startup.execution_callbacks(worker,permit)
    with pytest.raises(IntegrityError):startup.bootstrap(worker,permit,MIGRATIONS)
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM job_runs WHERE job_run_id=:id'),{'id':value['run'].job_run_id}).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0
    assert value['strategy'].calls==[]


@pytest.mark.parametrize('phase',['before_claim','after_claim','after_dispatch_commit'])
def test_crash_boundaries_are_durable_and_never_replay_or_fabricate_completion(prepare,phase):
    value=prepare();case=value['case'];worker=value['worker'];permit=case['verify_startup']()
    if phase=='before_claim':
        case['startup']['expires_at']=case['startup']['issued_at'];case['pin_authority']('startup')
        with pytest.raises(RuntimeError):startup.bootstrap(worker,permit,MIGRATIONS)
        with worker.connect() as connection:assert connection.execute(text('SELECT count(*) FROM paper_startup_claims')).scalar_one()==0
        return
    startup.bootstrap(worker,permit,MIGRATIONS)
    if phase=='after_claim':
        execution=case['verify_execution']();startup.execution_callbacks(worker,execution)
    else:
        def die(run):raise RuntimeError('synthetic loss after genuine financial commit')
        with pytest.raises(RuntimeError):execute(value,complete_override=die)
    assert observe(value)['state']=='RECOVERY_REQUIRED'
    with pytest.raises(IntegrityError):startup.execution_callbacks(worker,case['verify_execution']())
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_startup_completions')).scalar_one()==0
        outcomes=connection.execute(text('SELECT count(*) FROM paper_certified_outcomes WHERE job_run_id=:id'),{'id':value['run'].job_run_id}).scalar_one()
        assert outcomes==(1 if phase=='after_dispatch_commit' else 0)


@pytest.mark.parametrize('phase',['prepare','claim','commit'])
def test_revocation_or_expiry_inside_financial_transaction_rolls_back_all_effects(prepare,phase):
    value=prepare('FILLED');case=value['case'];worker=value['worker'];startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    execution=case['verify_execution']();check,txcheck,complete=startup.execution_callbacks(worker,execution)
    def revoked(connection,current):
        if current==phase:
            case['policy']['revoked_keys']=[execution.authorizer_key_sha256];case['policy_pin']()
        txcheck(connection,current)
    with pytest.raises(RuntimeError):
        service.run_service_cycle(worker,lambda *_:value['plan'],migrations_dir=MIGRATIONS,now=lambda:value['data']['as_of']+timedelta(minutes=1),
            require_certified=True,execution_check=check,expected_cycle_key=service.logical_cycle_key(value['run']),
            transaction_check=revoked,require_new_cycle=True,successful_run_observer=complete)
    with worker.connect() as connection:
        for table in ('paper_effects','paper_certified_outcomes','paper_startup_commit_witnesses','paper_startup_completions'):
            assert connection.execute(text('SELECT count(*) FROM '+table)).scalar_one()==0
        assert SqlAlchemyPaperStartupRepository(connection).observation(execution.base.authorization_sha256)['state']=='RECOVERY_REQUIRED'


def test_privileged_bootstrap_credentials_fail_before_consumption(startup_database,prepare):
    value=prepare();permit=value['case']['verify_startup']()
    with pytest.raises(RuntimeError,match='ROLE_PRIVILEGED'):startup.bootstrap(startup_database['admin'],permit,MIGRATIONS)
    with startup_database['admin'].connect() as connection:assert connection.execute(text('SELECT count(*) FROM paper_startup_claims')).scalar_one()==0


@pytest.mark.parametrize('table',['paper_startup_claims','paper_startup_bootstraps','paper_startup_executions','paper_startup_commit_witnesses','paper_startup_completions'])
def test_startup_state_is_append_only_even_for_database_owner(prepare,startup_database,table):
    value=prepare();startup.bootstrap(value['worker'],value['case']['verify_startup'](),MIGRATIONS);execute(value)
    with startup_database['admin'].begin() as connection:
        for sql in (f'UPDATE {table} SET evidence_sha256=evidence_sha256',f'DELETE FROM {table}',f'TRUNCATE {table} CASCADE'):
            with pytest.raises(DBAPIError,match='AUDIT_EVENT_IMMUTABLE'):
                with connection.begin_nested():connection.exec_driver_sql(sql)



@pytest.mark.parametrize('when',['before_bootstrap','before_dispatch','after_financial_commit'])
def test_halt_remains_authoritative_across_the_startup_transition(prepare,startup_database,when):
    from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository
    value=prepare();case=value['case'];worker=value['worker'];admin=startup_database['admin']
    def halt():
        with admin.begin() as connection:SqlAlchemyPaperEnvironmentControlRepository(connection).transition('RUNNING','HALTED','Synthetic startup HALT',actor='test')
    if when=='before_bootstrap':
        halt()
        with pytest.raises(RuntimeError,match='HALTED'):startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    else:
        startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
        execution=case['verify_execution']();check,txcheck,complete=startup.execution_callbacks(worker,execution)
        calls=[]
        def checked():
            calls.append(True)
            if when=='before_dispatch' and len(calls)==3:halt()
            check()
        def completed(run):
            if when=='after_financial_commit':halt()
            complete(run)
        with pytest.raises(RuntimeError,match='HALTED'):
            service.run_service_cycle(worker,lambda *_:value['plan'],migrations_dir=MIGRATIONS,now=lambda:value['data']['as_of']+timedelta(minutes=1),
                require_certified=True,execution_check=checked,expected_cycle_key=service.logical_cycle_key(value['run']),
                transaction_check=txcheck,require_new_cycle=True,successful_run_observer=completed)
    with worker.connect() as connection:
        assert connection.execute(text('SELECT state FROM paper_environment_control_events ORDER BY control_sequence DESC LIMIT 1')).scalar_one()=='HALTED'
        assert connection.execute(text('SELECT count(*) FROM paper_startup_completions')).scalar_one()==0
        outcomes=connection.execute(text('SELECT count(*) FROM paper_certified_outcomes WHERE job_run_id=:id'),{'id':value['run'].job_run_id}).scalar_one()
        assert outcomes==(1 if when=='after_financial_commit' else 0)
    # Tests alone restore their isolated control state; production code never does.
    with admin.begin() as connection:SqlAlchemyPaperEnvironmentControlRepository(connection).transition('HALTED','RUNNING','Disposable startup fixture cleanup',actor='test')


def test_competing_startup_consumers_have_exactly_one_committed_winner(prepare):
    from concurrent.futures import ThreadPoolExecutor
    value=prepare();permit=value['case']['verify_startup']();engine=value['worker']
    def contender():
        try:
            with engine.begin() as connection:SqlAlchemyPaperStartupRepository(connection).claim_startup(permit)
            return True
        except IntegrityError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:contender(),range(2)))
    assert sorted(results)==[False,True]
    with engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_startup_claims')).scalar_one()==1
        assert connection.execute(text('SELECT count(*) FROM paper_handoff_intents')).scalar_one()==1


@pytest.mark.parametrize('phase',['startup','financial'])
def test_real_commit_with_lost_acknowledgment_cannot_be_dispatched_again(prepare,phase):
    from sqlalchemy.exc import OperationalError,SQLAlchemyError
    value=prepare('FILLED');case=value['case'];worker=value['worker'];marked=[];lost=[]
    def wrote(connection,cursor,statement,parameters,context,executemany):
        target='INSERT INTO public.paper_startup_claims' if phase=='startup' else 'INSERT INTO public.paper_startup_commit_witnesses'
        if target in statement:marked.append(True)
    def lose_ack(connection):
        if marked and not lost:
            connection.connection.driver_connection.commit();lost.append(True)
            raise OperationalError('synthetic lost commit acknowledgment',{},RuntimeError('disposable test'))
    event.listen(worker,'after_cursor_execute',wrote);event.listen(worker,'commit',lose_ack)
    try:
        with pytest.raises(SQLAlchemyError):
            startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
            if phase=='financial':execute(value)
    finally:
        event.remove(worker,'after_cursor_execute',wrote);event.remove(worker,'commit',lose_ack)
    assert lost==[True]
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_startup_claims')).scalar_one()==1
        assert connection.execute(text('SELECT count(*) FROM paper_startup_completions')).scalar_one()==0
        outcomes=connection.execute(text('SELECT count(*) FROM paper_certified_outcomes WHERE job_run_id=:id'),{'id':value['run'].job_run_id}).scalar_one()
        assert outcomes==(1 if phase=='financial' else 0)
    with pytest.raises(IntegrityError):startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    assert len(value['strategy'].calls)==(1 if phase=='financial' else 0)


@pytest.mark.parametrize('fault',['catalog','controller','execution_scope','execution_expired','policy_revoked'])
def test_missing_or_contradictory_authority_never_creates_financial_effects(prepare,fault):
    value=prepare();case=value['case'];worker=value['worker']
    if fault=='catalog':case['startup']['schema_catalog_sha256']='0'*64;case['execution']['startup_authorization_sha256']=case['pin_authority']('startup');case['pin_authority']('execution')
    elif fault=='controller':case['token']['controller_process_sha256']='0'*64;case['pin']()
    if fault in {'catalog','controller'}:
        with pytest.raises(RuntimeError):startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    else:
        startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
        if fault=='execution_scope':case['execution']['scope']='BOOTSTRAP_ONLY';case['pin_authority']('execution')
        elif fault=='execution_expired':case['execution']['expires_at']=case['execution']['issued_at'];case['pin_authority']('execution')
        else:case['policy']['revoked_keys']=[authority.h.d._key_digest(case['root']/'runtime-trust'/authority.EXECUTION_ROLE)];case['policy_pin']()
        with pytest.raises(RuntimeError):case['verify_execution']()
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_startup_executions')).scalar_one()==0


def test_omitting_financial_commit_witness_is_rejected_by_deferred_database_constraint(prepare):
    value=prepare();case=value['case'];worker=value['worker'];startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    execution=case['verify_execution']();check,txcheck,complete=startup.execution_callbacks(worker,execution)
    def omit(connection,phase):
        if phase!='commit':txcheck(connection,phase)
    with pytest.raises(DBAPIError,match='FINANCIAL_COMMIT_UNVERIFIED'):
        service.run_service_cycle(worker,lambda *_:value['plan'],migrations_dir=MIGRATIONS,now=lambda:value['data']['as_of']+timedelta(minutes=1),
            require_certified=True,execution_check=check,expected_cycle_key=service.logical_cycle_key(value['run']),
            transaction_check=omit,require_new_cycle=True,successful_run_observer=complete)
    with worker.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM paper_certified_outcomes')).scalar_one()==0
        assert SqlAlchemyPaperStartupRepository(connection).observation(execution.base.authorization_sha256)['state']=='RECOVERY_REQUIRED'


@pytest.mark.parametrize('fault',['expanded_calendar','stale_clock','foreign_implementation'])
def test_startup_does_not_relax_calendar_freshness_or_separately_certified_implementation(prepare,fault):
    from hope.application.market_data.calendar import MarketSessionCalendar
    value=prepare();case=value['case'];worker=value['worker'];startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    execution=case['verify_execution']();check,txcheck,complete=startup.execution_callbacks(worker,execution)
    plan=value['plan'];current=value['data']['as_of']+timedelta(minutes=1)
    if fault=='expanded_calendar':
        instant=value['data']['as_of'];plan=replace(plan,end=instant+timedelta(hours=3),calendars={value['data']['market']:MarketSessionCalendar(((instant,instant+timedelta(hours=1)),(instant+timedelta(hours=2),instant+timedelta(hours=3))))})
    elif fault=='stale_clock':current=value['data']['as_of']+timedelta(minutes=3)
    else:
        binding=plan.bindings[0];work=replace(binding.work,definition=replace(binding.work.definition,code_commit='uncertified-bearish-implementation'))
        plan=replace(plan,bindings=(replace(binding,work=work),))
    with pytest.raises(RuntimeError):
        service.run_service_cycle(worker,lambda *_:plan,migrations_dir=MIGRATIONS,now=lambda:current,require_certified=True,
            execution_check=check,expected_cycle_key=service.logical_cycle_key(value['run']),transaction_check=txcheck,
            require_new_cycle=True,successful_run_observer=complete)
    with worker.connect() as connection:assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0



@pytest.mark.parametrize('execute_cycle',[False,True,'stopped_after_claim'])
def test_real_cli_route_bootstraps_without_current_and_requires_separate_execution_before_factory(prepare,tmp_path,monkeypatch,execute_cycle):
    import shutil,signal
    from unittest.mock import Mock
    from hope.api import paper_service as api,paper_controller as controller
    from hope.infrastructure.signal_stop import SignalStop
    value=prepare();case=value['case'];worker=value['worker'];release=case['root']/'releases'/SHA
    release.mkdir(parents=True);shutil.copytree(MIGRATIONS,release/'migrations')
    (case['root']/'current').symlink_to(release,target_is_directory=True)
    directory=tmp_path/'controller';controller.initialize(directory,SHA,RECEIPT,FACTORY)
    health=tmp_path/'health'/'health.json';factory_calls=[];cycle_errors=[]
    classify=service._failure_state
    def classification(error):
        cycle_errors.append((type(error).__name__,str(error)));return classify(error)
    monkeypatch.setattr(service,'_failure_state',classification)
    ordinary=Mock(side_effect=AssertionError('ordinary CURRENT gate must never be weakened or faked'))
    monkeypatch.setattr(api,'paper_operational_guard',ordinary)
    def factory(spec,**options):
        with worker.connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM paper_startup_executions')).scalar_one()==1
        factory_calls.append(spec)
        if execute_cycle=='stopped_after_claim':signal.raise_signal(signal.SIGTERM)
        return lambda *_:value['plan']
    monkeypatch.setattr(api,'load_plan_factory',factory)
    actual_startup=startup.run_startup
    def routed(args,release,database,stop):
        return actual_startup(args,release,database,stop,startup_verifier=lambda *a,**kw:case['verify_startup'](),
                              execution_verifier=lambda root,permit,request:case['verify_execution'](permit))
    monkeypatch.setattr(startup,'run_startup',routed)
    actual_service=service.run_service
    monkeypatch.setattr(service,'run_service',lambda engine,factory,**kw:actual_service(engine,factory,now=lambda:value['data']['as_of']+timedelta(minutes=1),**kw))
    monkeypatch.setenv('HOPE_DATABASE_URL',worker.url.render_as_string(hide_password=False));monkeypatch.setenv('HOPE_LIVE_ENABLED','false');monkeypatch.setenv('HOPE_DEPLOYED_COMMIT',SHA)
    args=['--factory',FACTORY,'--migrations-dir',str(release/'migrations'),'--status-file',str(health),'--once',
        '--deployment-receipt',RECEIPT,'--controller-directory',str(directory),'--handoff-request',case['token']['request_sha256'],
        '--expected-cycle-key',service.logical_cycle_key(value['run']),'--startup-request',case['startup']['request_sha256']]
    if execute_cycle:args+=['--first-cycle-execution-request',case['execution']['request_sha256']]
    previous={key:signal.getsignal(key) for key in (signal.SIGTERM,signal.SIGINT)}
    try:
        if execute_cycle=='stopped_after_claim':
            with pytest.raises(SystemExit) as rejected:api.main(args)
            assert rejected.value.code==2
        else:assert api.main(args)==0,cycle_errors
    finally:
        for key,handler in previous.items():signal.signal(key,handler)
    import json
    diagnostic=json.loads(Path(str(health)+'.startup.json').read_text())
    expected='RECOVERY_REQUIRED' if execute_cycle=='stopped_after_claim' else 'CYCLE_COMPLETED' if execute_cycle else 'BOOTSTRAPPED'
    assert diagnostic['state']==expected and diagnostic['execution_authorized'] is False
    assert factory_calls==([FACTORY] if execute_cycle else []) and len(value['strategy'].calls)==int(execute_cycle is True)
    ordinary.assert_not_called()
    if execute_cycle=='stopped_after_claim':
        result=json.loads(health.read_text());assert result['state']=='UNAVAILABLE' and result['cycles_succeeded']==0
        with worker.connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM paper_startup_executions')).scalar_one()==1
            assert connection.execute(text('SELECT count(*) FROM paper_startup_completions')).scalar_one()==0
            assert connection.execute(text('SELECT count(*) FROM job_runs WHERE job_run_id=:id'),{'id':value['run'].job_run_id}).scalar_one()==0
    elif execute_cycle:
        result=json.loads(health.read_text());assert result['state']=='STOPPED' and result['cycles_succeeded']==1
    else:assert not health.exists()



def test_witness_expiring_after_success_before_actual_commit_rolls_back_financial_transaction(prepare):
    import time
    value=prepare('FILLED');case=value['case'];worker=value['worker'];startup.bootstrap(worker,case['verify_startup'](),MIGRATIONS)
    # Isolate financial expiry before the separate five-second idle scheduler
    # connection expires. Do not loosen either production timeout or assertion.
    case['probes']['stage_probe']=lambda *a,**kw:dict(case['stage'],expires_at=int(time.time())+2)
    execution=case['verify_execution']();check,txcheck,complete=startup.execution_callbacks(worker,execution)
    def delayed(connection,phase):
        txcheck(connection,phase)
        if phase=='commit':connection.exec_driver_sql('SELECT pg_sleep(3)')
    with pytest.raises(DBAPIError,match='FINANCIAL_COMMIT_UNVERIFIED'):
        service.run_service_cycle(worker,lambda *_:value['plan'],migrations_dir=MIGRATIONS,now=lambda:value['data']['as_of']+timedelta(minutes=1),
            require_certified=True,execution_check=check,expected_cycle_key=service.logical_cycle_key(value['run']),
            transaction_check=delayed,require_new_cycle=True,successful_run_observer=complete)
    with worker.connect() as connection:
        for table in ('paper_effects','paper_certified_outcomes','paper_startup_commit_witnesses','paper_startup_completions'):
            assert connection.execute(text('SELECT count(*) FROM '+table)).scalar_one()==0



def test_temporary_financial_schema_shadow_is_refused_before_any_startup_consumption(prepare,startup_database):
    from hope.infrastructure.repositories.paper_startup import assert_public_schema
    value=prepare();worker=value['worker']
    with startup_database['admin'].begin() as connection:
        name=connection.dialect.identifier_preparer.quote(startup_database['admin'].url.database)
        connection.exec_driver_sql('GRANT TEMPORARY ON DATABASE '+name+' TO '+startup_database['role'])
    with worker.connect() as connection:
        connection.exec_driver_sql('CREATE TEMP TABLE paper_environment_control_events (state text)')
        with pytest.raises(RuntimeError):assert_public_schema(connection)
        connection.rollback()
    assert value['strategy'].calls==[]


def test_prebootstrapped_competing_authorities_cannot_open_two_unresolved_executions(prepare):
    from concurrent.futures import ThreadPoolExecutor
    first=prepare(receipt='1'*64);second=prepare(receipt='2'*64)
    for value in (first,second):startup.bootstrap(value['worker'],value['case']['verify_startup'](),MIGRATIONS)
    permits=[value['case']['verify_execution']() for value in (first,second)]
    def contender(permit):
        try:
            with first['worker'].begin() as connection:SqlAlchemyPaperStartupRepository(connection).claim_execution(permit)
            return True
        except IntegrityError as error:
            assert 'PAPER_STARTUP_EXECUTION_UNVERIFIED' in str(error)
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(contender,permits))
    assert sorted(results)==[False,True]
    with first['worker'].connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM paper_startup_executions')).scalar_one()==1
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==0
