"""Actual HOPE ledger, restricted read-only LOGIN; external approvals MODELED.

These are history/denial proofs, not positive independent production admission.
"""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from test_startup_transition_postgres import (
    isolated_database, startup_database, seeded, prepare, certified_case, execute, MIGRATIONS,
)
from hope.api import paper_startup as startup
from hope.domain.strategy.candidates import StrategyMarket
from hope.infrastructure.repositories.paper_handoff import database_identity
from hope.security.financial_lifecycle import (
    CycleReference, LifecycleDenied, dispatch_one_cycle, inspect_committed_cycle,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def observer(startup_database):
    admin = startup_database['admin']
    role, password = 'hope_observer_'+uuid4().hex, uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB "
            'NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO {role}')
        connection.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}')
        connection.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO {role}')
    engine = create_engine(admin.url.set(username=role, password=password))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP OWNED BY '+role)
            connection.exec_driver_sql('DROP ROLE '+role)


def completed(prepare, path='FILLED', market=StrategyMarket.USA):
    value = prepare(path, market)
    startup.bootstrap(value['worker'], value['case']['verify_startup'](), MIGRATIONS)
    _, permit = execute(value)
    with value['worker'].connect() as connection:
        xid = connection.execute(text('SELECT transaction_id::text FROM public.paper_startup_financial_transactions '
            'WHERE authorization_sha256=:auth'), {'auth': permit.authorization_sha256}).scalar_one()
        database = database_identity(connection)
        principal = connection.execute(text('SELECT session_user')).scalar_one()
    return value, CycleReference(database, permit.authorization_sha256, value['run'].job_run_id,
        xid, principal, value['data']['configuration']['paper_execution']['portfolio_id'])


@pytest.mark.parametrize('market', [StrategyMarket.USA, StrategyMarket.INDIA])
@pytest.mark.parametrize('path', ['FILLED', 'NO_TRADE', 'RISK_BLOCKED'])
def test_real_history_is_observed_without_granting_authority(prepare, observer, market, path):
    value, reference = completed(prepare, path, market)
    observed = inspect_committed_cycle(observer, reference)
    assert observed['history_consistent'] is True and observed['outcome'] == path
    assert observed['execution_authorized'] is False and observed['production_verification'] is False
    assert observed['replay_permitted'] is False
    assert observed['independent_session_attestation'] == 'BLOCKED'
    assert len(observed['effects']) == (5 if path == 'FILLED' else 1)
    # A report, copied worker Permit, factory or self-declared signer cannot dispatch.
    for request in (observed, value['case']['verify_execution'](), {'factory': lambda: pytest.fail('factory called')}):
        with pytest.raises(LifecycleDenied, match='INDEPENDENT_FINANCIAL_ADMISSION_UNAVAILABLE'):
            dispatch_one_cycle(request)
    with value['worker'].connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM public.paper_certified_outcomes '
            'WHERE job_run_id=:job'), {'job': reference.job_run_id}).scalar_one() == 1


@pytest.mark.parametrize('change', ['database', 'transaction', 'principal', 'authorization', 'job', 'portfolio'])
def test_borrowed_or_mismatched_reference_is_refused(prepare, observer, change):
    _, ref = completed(prepare)
    changed = {
        'database': dict(database_sha256='0'*64), 'transaction': dict(financial_xid=str(int(ref.financial_xid)+1)),
        'principal': dict(financial_principal='another_login'), 'authorization': dict(authorization_sha256='0'*64),
        'job': dict(job_run_id=uuid4()), 'portfolio': dict(portfolio_id=uuid4()),
    }
    with pytest.raises(LifecycleDenied):
        inspect_committed_cycle(observer, replace(ref, **changed[change]))


def test_writer_and_owner_are_not_read_only_observers(prepare, startup_database):
    value, ref = completed(prepare)
    for engine in (value['worker'], startup_database['admin']):
        with pytest.raises(LifecycleDenied, match='OBSERVER_'):
            inspect_committed_cycle(engine, ref)


def test_unknown_commit_with_missing_completion_remains_incomplete(prepare, observer):
    value = prepare('FILLED')
    startup.bootstrap(value['worker'], value['case']['verify_startup'](), MIGRATIONS)
    def crash(_run):
        raise RuntimeError('synthetic delivery loss after actual financial COMMIT')
    with pytest.raises(RuntimeError, match='synthetic delivery loss'):
        execute(value, complete_override=crash)
    with value['worker'].connect() as connection:
        row = connection.execute(text('SELECT authorization_sha256,transaction_id::text,database_principal '
            'FROM public.paper_startup_financial_transactions')).one()
        ref = CycleReference(database_identity(connection), row[0], value['run'].job_run_id, row[1], row[2],
            value['data']['configuration']['paper_execution']['portfolio_id'])
    with pytest.raises(LifecycleDenied, match='LIFECYCLE_INCOMPLETE'):
        inspect_committed_cycle(observer, ref)
    with value['worker'].connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM public.paper_effects')).scalar_one() == 5
        assert connection.execute(text('SELECT count(*) FROM public.paper_startup_completions')).scalar_one() == 0


def test_read_only_login_cannot_fund_portfolio_or_create_approval(prepare, observer):
    _, ref = completed(prepare)
    for statement in (
        'INSERT INTO public.paper_portfolios(portfolio_id,initial_cash,cash) VALUES (:id,100,100)',
        'UPDATE public.paper_portfolios SET initial_cash=initial_cash+100 WHERE portfolio_id=:id',
        "INSERT INTO public.paper_admission_approvals(target_table,authorization_sha256,principal_oid,approved_row,valid_until) "
        "VALUES ('paper_startup_executions',repeat('0',64),1,'{}',1)",
    ):
        with pytest.raises(DBAPIError) as caught:
            with observer.begin() as connection:
                connection.execute(text(statement), {'id': uuid4()})
        assert caught.value.orig.sqlstate == '42501'
    assert inspect_committed_cycle(observer, ref)['execution_authorized'] is False


@pytest.mark.parametrize('damage', ['audit', 'effect'])
def test_incomplete_restored_history_cannot_be_reported_complete(prepare, observer, startup_database, damage):
    _, ref = completed(prepare)
    # Explicit privileged corruption of a DISPOSABLE restored history. No
    # production guard is changed; ordinary workers cannot do this mutation.
    table = 'audit_events' if damage == 'audit' else 'paper_effects'
    with startup_database['admin'].begin() as connection:
        connection.exec_driver_sql('ALTER TABLE public.'+table+' DISABLE TRIGGER ALL')
        if damage == 'audit':
            connection.execute(text("DELETE FROM public.audit_events WHERE event_type='PAPER_RUN_RECONCILED' "
                'AND entity_id=:job'), {'job': str(ref.job_run_id)})
        else:
            connection.execute(text("DELETE FROM public.paper_effects WHERE job_run_id=:job AND effect_type='PNL'"),
                {'job': ref.job_run_id})
        connection.exec_driver_sql('ALTER TABLE public.'+table+' ENABLE TRIGGER ALL')
    with pytest.raises(LifecycleDenied, match='INCOMPLETE'):
        inspect_committed_cycle(observer, ref)
