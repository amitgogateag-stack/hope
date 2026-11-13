"""Experimental capture on REAL HOPE tables. External authority is MODELED.

The separate owner connection is a fixture oracle, not a production signer or
an independent observer of uncommitted data. Production admission stays DENY.
"""
from datetime import timedelta
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from test_startup_transition_postgres import (
    isolated_database, startup_database, seeded, prepare, certified_case, MIGRATIONS,
)
from hope.api import paper_startup as startup
from hope.domain.strategy.candidates import StrategyMarket
from hope.infrastructure import paper_service as service
from hope.security.write_set_witness import (
    PrototypeAuthorityRoot, WitnessDenied, authenticate_response, expected_body,
    canonical_body, capture_snapshot, inspect_reservation, production_approval,
)

pytestmark = pytest.mark.integration
SCHEMA = Path(__file__).parents[2]/'security/transaction_witness/schema.sql'


def reserve(admin, connection, authorization, outcome, *, ttl=None, alter=None):
    identity = connection.execute(text('SELECT pg_current_xact_id()::text AS transaction_id,pg_backend_pid() AS backend_pid, '
        '(SELECT backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()) AS backend_start, '
        'session_user AS principal_name,(SELECT oid FROM pg_database WHERE datname=current_database()) AS database_oid, '
        '(SELECT system_identifier::text FROM pg_control_system()) AS cluster_id')).mappings().one()
    with admin.begin() as root:
        root.execute(text('SELECT pg_stat_clear_snapshot()'))
        authority = root.execute(text('SELECT job_run_id,observed_policy_generation AS generation, '
            'least(expires_at,extract(epoch FROM clock_timestamp())::bigint+14) AS expires_at FROM public.paper_startup_executions '
            'WHERE authorization_sha256=:auth'), {'auth': authorization}).mappings().one()
        values = dict(identity, **dict(authority), authorization_sha256=authorization, outcome=outcome)
        if ttl is not None:
            values['expires_at'] = min(values['expires_at'], root.execute(text(
                'SELECT extract(epoch FROM clock_timestamp())::bigint+:ttl'), {'ttl': ttl}).scalar_one())
        if alter: values.update(alter)
        return root.execute(text('INSERT INTO financial_witness.claims(authorization_sha256,job_run_id,principal_name, '
            'transaction_id,backend_pid,backend_start,database_oid,cluster_id,generation,expires_at,outcome) '
            'VALUES (:authorization_sha256,:job_run_id,:principal_name,CAST(:transaction_id AS xid8),:backend_pid, '
            ':backend_start,:database_oid,:cluster_id,:generation,:expires_at,:outcome) RETURNING nonce'), values).scalar_one()


@pytest.fixture
def witness_case(startup_database, prepare):
    admin = startup_database['admin']; worker = startup_database['worker']; role = startup_database['role']
    with admin.begin() as connection:
        connection.execute(text(SCHEMA.read_text()))
        connection.execute(text('INSERT INTO financial_witness.principals VALUES (:role)'), {'role': role})
        connection.exec_driver_sql('GRANT USAGE ON SCHEMA financial_witness TO '+role)
        for function in ('open_envelope(uuid)', 'snapshot(uuid)', 'challenge(uuid)', 'seal(uuid)', 'digest(jsonb)'):
            connection.exec_driver_sql('GRANT EXECUTE ON FUNCTION financial_witness.'+function+' TO '+role)
    def build(path='FILLED', market=StrategyMarket.USA):
        # Generate fixture-only keys before acquiring the short-lived actual
        # execution witness. Do not refresh or extend its deadline.
        private = Ed25519PrivateKey.generate()
        root = PrototypeAuthorityRoot(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
            'MODELED_EXTERNAL_TEST_AUTHORITY', 'a'*64, 'b'*64)
        value = prepare(path, market)
        with admin.begin() as connection:
            connection.execute(text('UPDATE financial_witness.policy SET generation=:generation'),
                {'generation':value['case']['policy']['generation']})
        startup.bootstrap(worker, value['case']['verify_startup'](), MIGRATIONS)
        permit = value['case']['verify_execution']()
        check, original, complete = startup.execution_callbacks(worker, permit)
        state = dict(value=value, permit=permit, root=root, private=private, outcome=path, nonce=None)
        def run(*, manifest=None, signed_change=None, wrong_key=False, omit_approval=False, omit_seal=False,
                ttl=None, after_seal=None, completion=None):
            def transaction(connection, phase):
                if phase == 'prepare':
                    state['nonce'] = reserve(admin, connection, permit.authorization_sha256, path, ttl=ttl)
                    connection.execute(text('SELECT financial_witness.open_envelope(:nonce)'), {'nonce': state['nonce']})
                original(connection, phase)
                if phase != 'commit': return
                # Ordinary successful cycles do not create reconciliation
                # audit events. Exercise capture with a clearly experimental
                # event, without fabricating a reconciliation receipt.
                connection.execute(text("INSERT INTO public.audit_events "
                    "(audit_event_id,event_type,entity_type,entity_id,payload) "
                    "VALUES (gen_random_uuid(),'PROTOTYPE_WRITE_SET_TEST','JOB_RUN',:job,'{\"prototype_only\":true}')"),
                    {'job':str(permit.run.job_run_id)})
                nonce = state['nonce']; raw, actual_digest = capture_snapshot(connection, nonce)
                expected = raw if manifest is None else json.dumps(manifest(json.loads(raw)), separators=(',', ':'))
                with admin.connect() as owner:
                    digest = owner.execute(text('SELECT financial_witness.digest(CAST(:events AS jsonb))'), {'events': expected}).scalar_one()
                challenge = connection.execute(text('SELECT financial_witness.challenge(:nonce)'), {'nonce': nonce}).scalar_one()
                body = expected_body(challenge, digest, root)
                if signed_change: signed_change(body)
                signature = (Ed25519PrivateKey.generate() if wrong_key else private).sign(canonical_body(body)).hex()
                response = json.dumps(dict(body=body, signature=signature), separators=(',', ':')).encode()
                state.update(response=response, digest=actual_digest, raw=raw)
                authenticated = authenticate_response(connection, nonce, digest, response, root)
                assert authenticated['production_authorized'] is False
                if not omit_approval:
                    # EXPLICIT external-authority MODEL. Owner receives the
                    # trusted harness's expected records, not a production RPC.
                    with admin.begin() as owner:
                        owner.execute(text('INSERT INTO financial_witness.approvals VALUES '
                            '(:nonce,CAST(:expected AS jsonb),:response)'),
                            {'nonce': nonce, 'expected': expected, 'response': authenticated['response_sha256']})
                if not omit_seal:
                    connection.execute(text('SELECT financial_witness.seal(:nonce)'), {'nonce': nonce})
                if after_seal: after_seal(connection, state)
            return service.run_service_cycle(worker, lambda *_: value['plan'], migrations_dir=MIGRATIONS,
                now=lambda: value['data']['as_of']+timedelta(minutes=1), require_certified=True,
                execution_check=check, expected_cycle_key=service.logical_cycle_key(value['run']),
                transaction_check=transaction, require_new_cycle=True, successful_run_observer=completion or complete)
        state['run'] = run
        return state
    return build


def recovery(admin, state):
    with admin.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        return inspect_reservation(connection, state['permit'].authorization_sha256)


def zero_effects(admin):
    with admin.connect() as connection:
        for table in ('paper_effects','orders','fills','paper_certified_outcomes','financial_witness.events', 'financial_witness.seals'):
            name = table if '.' in table else 'public.'+table
            assert connection.exec_driver_sql('SELECT count(*) FROM '+name).scalar_one() == 0
        assert connection.execute(text('SELECT count(*) FROM financial_witness.claims')).scalar_one() == 1


@pytest.mark.parametrize('market', [StrategyMarket.USA, StrategyMarket.INDIA])
@pytest.mark.parametrize('path', ['FILLED','NO_TRADE','RISK_BLOCKED'])
def test_real_cycle_commits_full_row_write_set_and_never_authorizes_replay(witness_case, startup_database, path, market):
    state = witness_case(path, market)
    assert state['run']() == (1, 0)
    result = recovery(startup_database['admin'], state)
    assert result == dict(schema='hope.prototype-write-set-recovery.v1',state='FINANCIAL_COMMIT_RECORDED',
        execution_authorized=False,replay_permitted=False,production_verification=False)
    rows = json.loads(state['raw']); relations = {r['relation'] for r in rows}
    assert {'public.job_runs','public.audit_events','public.paper_effects','public.paper_certified_outcomes',
        'public.paper_startup_financial_transactions','public.paper_startup_commit_witnesses'} <= relations
    if path == 'FILLED':
        assert {'public.signals','public.orders','public.fills','public.paper_portfolios',
            'public.paper_portfolio_positions','public.paper_portfolio_pnl_events','public.paper_portfolio_fill_applications'} <= relations
        assert any(r['operation']=='UPDATE' and r['relation']=='public.paper_portfolios' and r['before'] is not None for r in rows)
    else:
        assert 'public.paper_no_trade_decisions' in relations and 'public.fills' not in relations
    with pytest.raises(WitnessDenied, match='INDEPENDENT_WRITE_SET_AUTHORITY_UNAVAILABLE'):
        production_approval(result)


@pytest.mark.parametrize('field', ['backend_pid','transaction_id','database_oid','cluster_id','principal_name','job_run_id','nonce'])
def test_signed_borrowed_context_is_refused(witness_case, startup_database, field):
    state = witness_case()
    def change(body):
        value = body['challenge'][field]
        body['challenge'][field] = value+1 if type(value) is int else str(value)+'-borrowed'
    with pytest.raises(WitnessDenied, match='WRITE_SET_RESPONSE_BINDING_MISMATCH'):
        state['run'](signed_change=change)
    zero_effects(startup_database['admin'])


@pytest.mark.parametrize('field', ['issuer','role','scope','release_sha256','runtime_image_sha256','writes_sha256'])
def test_signed_wrong_authority_contract_is_refused(witness_case, startup_database, field):
    state = witness_case()
    with pytest.raises(WitnessDenied, match='WRITE_SET_RESPONSE_BINDING_MISMATCH'):
        state['run'](signed_change=lambda body: body.update({field:'0'*64}))
    zero_effects(startup_database['admin'])


def test_forged_response_signature_rolls_back_actual_financial_work(witness_case, startup_database):
    state = witness_case()
    with pytest.raises(WitnessDenied, match='WRITE_SET_RESPONSE_SIGNATURE_INVALID'):
        state['run'](wrong_key=True)
    zero_effects(startup_database['admin'])


@pytest.mark.parametrize('change', ['missing','extra','wrong_quantity','wrong_relation','wrong_before'])
def test_exact_manifest_refuses_incomplete_extra_and_altered_rows(witness_case, startup_database, change):
    state = witness_case()
    def mutate(rows):
        if change=='missing': return rows[:-1]
        if change=='extra': return rows+[rows[-1]]
        target = next(r for r in rows if r['relation']=='public.fills')
        if change=='wrong_quantity': target['after']['quantity']=999
        elif change=='wrong_relation': target['relation']='public.unapproved_finance'
        else: target['before']={'fabricated':True}
        return rows
    with pytest.raises(DBAPIError, match='WRITE_SET_MANIFEST_MISMATCH'):
        state['run'](manifest=mutate)
    zero_effects(startup_database['admin'])


@pytest.mark.parametrize('mode', ['missing_approval','missing_seal','after_seal_change','early_constraints','expired','revoked','generation'])
def test_final_commit_fences_reject_unproved_transactions(witness_case, startup_database, mode):
    state = witness_case(); options = {}; reason = {
        'missing_approval':'WRITE_SET_INDEPENDENT_APPROVAL_UNAVAILABLE','missing_seal':'WRITE_SET_SEAL_MISSING',
        'after_seal_change':'WRITE_SET_SEAL_MISMATCH','early_constraints':'WRITE_SET_ACTUAL_COMMIT_REQUIRED',
        'expired':'WRITE_SET_AUTHORITY_EXPIRED','revoked':'WRITE_SET_POLICY_REVOKED','generation':'WRITE_SET_POLICY_REVOKED'}[mode]
    if mode=='missing_approval': options['omit_approval']=True
    if mode=='missing_seal': options['omit_seal']=True
    def after(connection, value):
        if mode=='after_seal_change':
            connection.execute(text('UPDATE public.paper_portfolios SET cash=cash WHERE portfolio_id=:id'),
                {'id': value['value']['data']['configuration']['paper_execution']['portfolio_id']})
        elif mode=='early_constraints': connection.exec_driver_sql('SET CONSTRAINTS ALL IMMEDIATE')
        elif mode=='expired':
            expiry = connection.execute(text('SELECT (financial_witness.challenge(:nonce)->>\'expires_at\')::bigint'),
                {'nonce': value['nonce']}).scalar_one()
            while connection.execute(text('SELECT clock_timestamp()<to_timestamp(:expiry)'), {'expiry':expiry}).scalar_one():
                connection.execute(text('SELECT pg_sleep(0.1)'))
        elif mode in {'revoked','generation'}:
            with startup_database['admin'].begin() as root:
                root.exec_driver_sql('UPDATE financial_witness.policy SET '+('revoked=true' if mode=='revoked' else 'generation=generation+1'))
    if mode=='expired': options['ttl']=10
    options['after_seal']=after
    with pytest.raises(DBAPIError, match=reason): state['run'](**options)
    zero_effects(startup_database['admin'])
    assert recovery(startup_database['admin'],state)['state']=='RECOVERY_REQUIRED'


def test_committed_witness_survives_loss_of_completion_without_replay(witness_case, startup_database):
    state = witness_case()
    def lost(_run): raise RuntimeError('injected loss after actual financial COMMIT')
    with pytest.raises(RuntimeError, match='injected loss'):
        state['run'](completion=lost)
    assert recovery(startup_database['admin'],state)['state']=='FINANCIAL_COMMIT_RECORDED'
    with startup_database['admin'].connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM public.paper_startup_completions')).scalar_one()==0
        assert connection.execute(text('SELECT count(*) FROM public.paper_effects')).scalar_one()==5
    with state['value']['worker'].begin() as connection:
        with pytest.raises(DBAPIError, match='WRITE_SET_SESSION_MISMATCH'):
            with connection.begin_nested():
                connection.execute(text('SELECT financial_witness.open_envelope(:nonce)'), {'nonce':state['nonce']})


def test_worker_cannot_forge_capture_or_owner_approval_even_with_insert_misgrant(witness_case, startup_database):
    state = witness_case(); admin = startup_database['admin']; worker = state['value']['worker']; role = startup_database['role']
    with admin.begin() as connection:
        connection.exec_driver_sql('GRANT INSERT ON ALL TABLES IN SCHEMA financial_witness TO '+role)
    def attack(connection, value):
        with pytest.raises(DBAPIError, match='WRITE_SET_INTERNAL_WRITER_REQUIRED'):
            with connection.begin_nested():
                connection.execute(text('INSERT INTO financial_witness.seals VALUES (:nonce,pg_current_xact_id(),1,repeat(\'0\',64))'),
                    {'nonce':value['nonce']})
        with pytest.raises(DBAPIError, match='WRITE_SET_EXTERNAL_OWNER_REQUIRED'):
            with connection.begin_nested():
                connection.execute(text("INSERT INTO financial_witness.approvals VALUES (:nonce,'[]',repeat('0',64))"),
                    {'nonce':value['nonce']})
    assert state['run'](after_seal=attack)==(1,0)
    assert recovery(admin,state)['state']=='FINANCIAL_COMMIT_RECORDED'


def test_capture_inventory_covers_all_registered_relations(witness_case, startup_database):
    witness_case()
    with startup_database['admin'].connect() as connection:
        rows = connection.execute(text("SELECT c.relname,t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_class c "
            "ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND t.tgname IN ('witness_capture','witness_no_truncate')")).all()
    assert len(rows)==34 and all(r[2]=='O' for r in rows)


def test_rollback_keeps_reservation_consumed_and_refuses_new_transaction(witness_case,startup_database):
    state=witness_case()
    with pytest.raises(WitnessDenied,match='SIGNATURE_INVALID'):
        state['run'](wrong_key=True)
    zero_effects(startup_database['admin'])
    with state['value']['worker'].begin() as connection:
        with pytest.raises(DBAPIError,match='WRITE_SET_SESSION_MISMATCH'):
            with connection.begin_nested():
                connection.execute(text('SELECT financial_witness.open_envelope(:nonce)'),{'nonce':state['nonce']})
        with pytest.raises(DBAPIError,match='duplicate key'):
            reserve(startup_database['admin'],connection,state['permit'].authorization_sha256,'FILLED')
    assert recovery(startup_database['admin'],state)['state']=='RECOVERY_REQUIRED'


def test_owner_cannot_rewrite_durable_reservation(witness_case,startup_database):
    state=witness_case()
    with pytest.raises(WitnessDenied,match='SIGNATURE_INVALID'):
        state['run'](wrong_key=True)
    with startup_database['admin'].begin() as connection:
        for statement in ('UPDATE financial_witness.claims SET expires_at=expires_at+1',
                          'DELETE FROM financial_witness.claims','TRUNCATE financial_witness.approvals'):
            with pytest.raises(DBAPIError,match='WRITE_SET_AUDIT_IMMUTABLE'):
                with connection.begin_nested(): connection.exec_driver_sql(statement)
    zero_effects(startup_database['admin'])
