"""Real signatures and disposable PG audit; stage/runtime witnesses are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.exc import DBAPIError,IntegrityError

from first_cycle_fixture import signed_case,SHA,RECEIPT,FACTORY
from hope.api import first_cycle_authorization as a
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository
from hope.infrastructure.repositories.paper_handoff import SqlAlchemyPaperHandoffRepository,database_identity

pytestmark=pytest.mark.integration


@pytest.fixture(scope='module')
def database():
    url=os.getenv('HOPE_DATABASE_URL')
    if not url or os.name!='posix':pytest.skip('Disposable PostgreSQL 16/17 Linux CI required')
    engine=create_engine(url,connect_args={'options':'-c statement_timeout=10000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=5000'})
    try:
        with engine.begin() as connection:apply_migrations(connection,Path(__file__).parents[2]/'migrations')
        yield engine
    finally:engine.dispose()


@pytest.fixture
def audit_case(database,tmp_path,monkeypatch):
    case=signed_case(tmp_path,monkeypatch)
    with database.connect() as connection:identity=database_identity(connection)
    case['token'].update(database_sha256=identity,request_sha256=a.digest(uuid4().hex),first_cycle_key_sha256=a.digest(uuid4().hex))
    case['pin']()
    return database,case


def count(connection,proof):
    return connection.execute(text('SELECT count(*) FROM paper_handoff_intents WHERE authorization_sha256=:auth'),
                              {'auth':proof.authorization_sha256}).scalar_one()


@pytest.mark.parametrize('outcome',['ACKNOWLEDGED','ABORTED'])
def test_one_use_survives_reconnect_and_outcome_never_authorizes_execution(audit_case,outcome):
    engine,case=audit_case;proof=case['verify']()
    with engine.begin() as connection:
        repository=SqlAlchemyPaperHandoffRepository(connection)
        before=connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()
        assert repository.observation(proof.authorization_sha256)['state']=='ABSENT'
        assert repository.claim(proof)==proof.authorization_sha256
        assert repository.observation(proof.authorization_sha256)['state']=='RECOVERY_REQUIRED'
        repository.finish(proof,outcome)
        report=repository.observation(proof.authorization_sha256)
        assert report['state']==outcome and report['execution_authorized'] is False
        assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()==before
    with engine.begin() as connection:
        repository=SqlAlchemyPaperHandoffRepository(connection)
        assert repository.observation(proof.authorization_sha256)['state']==outcome
        with pytest.raises(IntegrityError):repository.claim(proof)
        with pytest.raises(IntegrityError):repository.finish(proof,outcome)
        assert count(connection,proof)==1


def test_competing_transactions_have_exactly_one_durable_claim(audit_case):
    engine,case=audit_case;proof=case['verify']()
    def contender():
        try:
            with engine.begin() as connection:SqlAlchemyPaperHandoffRepository(connection).claim(proof)
            return 'claimed'
        except DBAPIError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:contender(),range(2)))
    assert sorted(results)==['claimed','rejected']
    with engine.connect() as connection:
        assert count(connection,proof)==1
        assert SqlAlchemyPaperHandoffRepository(connection).observation(proof.authorization_sha256)['state']=='RECOVERY_REQUIRED'


def test_rollback_is_not_consumption_but_committed_uncertainty_cannot_be_replayed(audit_case):
    engine,case=audit_case;proof=case['verify']()
    with engine.connect() as connection:
        transaction=connection.begin()
        SqlAlchemyPaperHandoffRepository(connection).claim(proof);transaction.rollback()
    with engine.begin() as connection:SqlAlchemyPaperHandoffRepository(connection).claim(proof)
    with engine.begin() as connection:
        repository=SqlAlchemyPaperHandoffRepository(connection)
        assert repository.observation(proof.authorization_sha256)['state']=='RECOVERY_REQUIRED'
        with pytest.raises(IntegrityError):repository.claim(proof)


@pytest.mark.parametrize('fault',['database','revoked','expired','owner'])
def test_invalid_authority_never_reaches_audit_write(audit_case,fault):
    engine,case=audit_case;proof=case['verify']()
    if fault=='database':case['token']['database_sha256']='0'*64;case['pin']();proof=case['verify']()
    elif fault=='revoked':case['policy']['revoked_keys']=[proof.authorizer_key_sha256];case['policy_pin']()
    elif fault=='expired':case['token']['expires_at']=int(time.time())-1;case['pin']()
    else:case['token']['controller_process_sha256']='0'*64;case['pin']()
    with engine.begin() as connection:
        with pytest.raises(RuntimeError):SqlAlchemyPaperHandoffRepository(connection).claim(proof)
        assert count(connection,proof)==0


@pytest.mark.parametrize('isolation',['REPEATABLE READ','SERIALIZABLE'])
def test_stale_control_snapshots_are_refused_by_repository(audit_case,isolation):
    engine,case=audit_case;proof=case['verify']()
    with engine.connect().execution_options(isolation_level=isolation) as connection,connection.begin():
        with pytest.raises(RuntimeError,match='READ_COMMITTED'):SqlAlchemyPaperHandoffRepository(connection).claim(proof)
        assert count(connection,proof)==0


def test_halt_and_missing_or_poisoned_control_never_admit_handoff(audit_case):
    engine,case=audit_case;proof=case['verify']()
    with engine.connect() as connection,connection.begin() as outer:
        control=SqlAlchemyPaperEnvironmentControlRepository(connection)
        control.transition('RUNNING','HALTED','Synthetic handoff refusal',actor='test')
        with pytest.raises(RuntimeError,match='HALTED'):SqlAlchemyPaperHandoffRepository(connection).claim(proof)
        assert count(connection,proof)==0
        outer.rollback()


@pytest.mark.parametrize('setting',['statement_timeout','lock_timeout','idle_in_transaction_session_timeout'])
def test_unbounded_database_waits_are_refused_before_consumption(audit_case,setting):
    engine,case=audit_case;proof=case['verify']()
    with engine.begin() as connection:
        connection.exec_driver_sql('SET LOCAL '+setting+'=0')
        with pytest.raises(RuntimeError):SqlAlchemyPaperHandoffRepository(connection).claim(proof)
        assert count(connection,proof)==0


def test_policy_generations_cannot_roll_back_after_a_committed_claim(audit_case):
    engine,case=audit_case
    case['policy']['generation']=8;case['policy_pin']();proof=case['verify']()
    with engine.begin() as connection:SqlAlchemyPaperHandoffRepository(connection).claim(proof)
    case['token'].update(request_sha256=a.digest(uuid4().hex),first_cycle_key_sha256=a.digest(uuid4().hex));case['pin']()
    case['policy']['generation']=7;case['policy_pin']();older=case['verify']()
    with engine.begin() as connection:
        with pytest.raises(IntegrityError,match='POLICY_ROLLBACK'):SqlAlchemyPaperHandoffRepository(connection).claim(older)
        assert count(connection,older)==0


@pytest.mark.parametrize('duplicate',['request_sha256','first_cycle_key_sha256'])
def test_resigned_documents_cannot_reuse_an_already_consumed_request_or_cycle(audit_case,duplicate):
    engine,case=audit_case;proof=case['verify']()
    with engine.begin() as connection:SqlAlchemyPaperHandoffRepository(connection).claim(proof)
    other='first_cycle_key_sha256' if duplicate=='request_sha256' else 'request_sha256'
    case['token'][other]=a.digest(uuid4().hex);case['pin']();new=case['verify']()
    assert new.authorization_sha256!=proof.authorization_sha256
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):SqlAlchemyPaperHandoffRepository(connection).claim(new)
        assert count(connection,new)==0


@pytest.mark.parametrize('table',['paper_handoff_intents','paper_handoff_outcomes'])
@pytest.mark.parametrize('operation',['UPDATE','DELETE','TRUNCATE'])
def test_audit_rows_cannot_be_rewritten_removed_or_truncated(audit_case,table,operation):
    engine,case=audit_case;proof=case['verify']()
    with engine.connect() as connection,connection.begin() as outer:
        repository=SqlAlchemyPaperHandoffRepository(connection);repository.claim(proof);repository.finish(proof,'ACKNOWLEDGED')
        sql=(f'UPDATE {table} SET controller_process_sha256=controller_process_sha256 WHERE authorization_sha256=:auth'
             if operation=='UPDATE' else f'DELETE FROM {table} WHERE authorization_sha256=:auth'
             if operation=='DELETE' else f'TRUNCATE {table} CASCADE')
        with pytest.raises(IntegrityError,match='AUDIT_EVENT_IMMUTABLE'):
            with connection.begin_nested():connection.execute(text(sql),{'auth':proof.authorization_sha256})
        assert count(connection,proof)==1
        outer.rollback()


def test_expiry_during_post_insert_revalidation_rolls_back_the_pending_claim(audit_case,monkeypatch):
    engine,case=audit_case;proof=case['verify']();original=a.VerifiedHandoff.assert_current;calls=[]
    def interrupted(self):
        calls.append(True)
        if len(calls)==2:raise RuntimeError('synthetic revocation after INSERT')
        return original(self)
    monkeypatch.setattr(a.VerifiedHandoff,'assert_current',interrupted)
    with engine.begin() as connection:
        with pytest.raises(RuntimeError):SqlAlchemyPaperHandoffRepository(connection).claim(proof)
        assert count(connection,proof)==0


def test_another_process_cannot_present_the_original_controller_authority(audit_case):
    engine,case=audit_case;proof=case['verify']()
    script='''
import os,sys
from hope.api import first_cycle_authorization as a,runtime_integrity as r
r.OWNER=os.geteuid()
try:
    a.verify(sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5],stage_probe=lambda *a,**k:None,runtime_probe=lambda *a:None)
except RuntimeError:raise SystemExit(0)
raise SystemExit(7)
'''
    child=subprocess.run([sys.executable,'-c',script,str(case['root']),SHA,case['token']['request_sha256'],RECEIPT,FACTORY],
                         capture_output=True,text=True,timeout=10)
    assert child.returncode==0 and not child.stdout
    with engine.connect() as connection:assert count(connection,proof)==0


@pytest.mark.parametrize('fault',['cycles','live','paper','frozen','hash','future','expired','witness','same_process'])
def test_direct_sql_cannot_relax_the_handoff_row_contract(audit_case,fault):
    engine,case=audit_case;proof=case['verify']()
    with engine.connect() as connection,connection.begin() as outer:
        repository=SqlAlchemyPaperHandoffRepository(connection);repository.claim(proof)
        values=dict(connection.execute(text('SELECT * FROM public.paper_handoff_intents WHERE authorization_sha256=:auth'),
                                       {'auth':proof.authorization_sha256}).mappings().one())
        values.update(authorization_sha256=a.digest(uuid4().hex),request_sha256=a.digest(uuid4().hex),first_cycle_key_sha256=a.digest(uuid4().hex))
        if fault=='cycles':values['max_cycles']=2
        elif fault=='live':values['live_enabled']=True
        elif fault=='paper':values['paper_only']=False
        elif fault=='frozen':values['release_commit']='7e9d341742818a97accce3e2301b1c7f96596795'
        elif fault=='hash':values['authorization_sha256']='A'*64
        elif fault=='future':values.update(issued_at=int(time.time())+10,expires_at=int(time.time())+30,witness_expires_at=int(time.time())+20)
        elif fault=='expired':values['witness_expires_at']=int(time.time())-1
        elif fault=='witness':values['witness_expires_at']=values['expires_at']+1
        else:values['stage_process_sha256']=values['controller_process_sha256']
        columns=','.join(values);params=','.join(':'+key for key in values)
        with pytest.raises(IntegrityError):
            with connection.begin_nested():connection.execute(text(f'INSERT INTO public.paper_handoff_intents ({columns}) VALUES ({params})'),values)
        assert count(connection,proof)==1
        outer.rollback()


def test_temporary_tables_cannot_shadow_the_durable_replay_fence(audit_case):
    engine,case=audit_case;proof=case['verify']()
    with engine.connect() as connection,connection.begin() as outer:
        connection.exec_driver_sql('CREATE TEMP TABLE paper_handoff_intents (LIKE public.paper_handoff_intents INCLUDING ALL)')
        connection.exec_driver_sql('CREATE TEMP TABLE paper_handoff_outcomes (LIKE public.paper_handoff_outcomes INCLUDING ALL)')
        repository=SqlAlchemyPaperHandoffRepository(connection);repository.claim(proof);repository.finish(proof,'ACKNOWLEDGED')
        assert connection.execute(text('SELECT count(*) FROM pg_temp.paper_handoff_intents')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM pg_temp.paper_handoff_outcomes')).scalar_one()==0
        assert repository.observation(proof.authorization_sha256)['state']=='ACKNOWLEDGED'
        outer.rollback()


def test_trigger_reads_real_halt_even_if_temporary_control_history_is_forged(audit_case):
    engine,case=audit_case;proof=case['verify']()
    with engine.connect() as connection,connection.begin() as outer:
        SqlAlchemyPaperEnvironmentControlRepository(connection).transition('RUNNING','HALTED','Synthetic shadow refusal',actor='test')
        connection.exec_driver_sql('CREATE TEMP TABLE paper_environment_control_events AS SELECT * FROM public.paper_environment_control_events')
        connection.exec_driver_sql("UPDATE pg_temp.paper_environment_control_events SET state='RUNNING'")
        with pytest.raises(IntegrityError,match='CONTROL_NOT_READY'):SqlAlchemyPaperHandoffRepository(connection).claim(proof)
        assert count(connection,proof)==0
        outer.rollback()


@pytest.mark.parametrize('fault',['owner','evidence','policy','expired','orphan'])
def test_outcome_requires_the_exact_existing_claim_and_current_witness(audit_case,fault):
    engine,case=audit_case;proof=case['verify']()
    with engine.connect() as connection,connection.begin() as outer:
        repository=SqlAlchemyPaperHandoffRepository(connection);repository.claim(proof)
        values=dict(auth=proof.authorization_sha256,owner=proof.token['controller_process_sha256'],
                    evidence=proof.evidence_sha256,policy=proof.policy_authority_sha256,generation=proof.policy_generation,
                    expiry=proof.expires_at)
        if fault=='owner':values['owner']='0'*64
        elif fault=='evidence':values['evidence']='0'*64
        elif fault=='policy':values['policy']='0'*64
        elif fault=='expired':values['expiry']=int(time.time())-1
        else:values['auth']='0'*64
        with pytest.raises(IntegrityError,match='OUTCOME_UNVERIFIED'):
            with connection.begin_nested():
                connection.execute(text("INSERT INTO public.paper_handoff_outcomes(authorization_sha256,controller_process_sha256,evidence_sha256,"
                    "policy_authority_sha256,observed_policy_generation,outcome,witness_expires_at) VALUES (:auth,:owner,:evidence,:policy,:generation,'ACKNOWLEDGED',:expiry)"),values)
        assert repository.observation(proof.authorization_sha256)['state']=='RECOVERY_REQUIRED'
        outer.rollback()


def test_outcome_advances_the_durable_policy_generation_fence(audit_case):
    engine,case=audit_case;proof=case['verify']()
    with engine.begin() as connection:SqlAlchemyPaperHandoffRepository(connection).claim(proof)
    case['policy']['generation']=8;case['policy_pin']()
    with engine.begin() as connection:SqlAlchemyPaperHandoffRepository(connection).finish(proof,'ACKNOWLEDGED')
    case['token'].update(request_sha256=a.digest(uuid4().hex),first_cycle_key_sha256=a.digest(uuid4().hex));case['pin']()
    case['policy']['generation']=7;case['policy_pin']();old=case['verify']()
    with engine.begin() as connection:
        with pytest.raises(IntegrityError,match='POLICY_ROLLBACK'):SqlAlchemyPaperHandoffRepository(connection).claim(old)
