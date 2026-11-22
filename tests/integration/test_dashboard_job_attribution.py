"""Real read-only observations of untrusted legacy receipt/job relationships.

These private unconstrained tables deliberately model corrupt historical input,
not independent admission or operational PAPER execution.
"""
from uuid import uuid4

import pytest
from sqlalchemy import text

from hope.api.dashboard_snapshot import _optional_projections

pytestmark = pytest.mark.integration


@pytest.fixture
def market():
    return "USA"


@pytest.fixture
def legacy_receipt_case(postgres_engine, market):
    schema = 'dashboard_job_' + uuid4().hex
    order, fill, instrument = uuid4(), uuid4(), uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        for statement in (
            'CREATE TABLE instruments(instrument_id uuid,exchange text)',
            'CREATE TABLE orders(order_id uuid,instrument_id uuid,environment text)',
            'CREATE TABLE fills(fill_id uuid,order_id uuid)',
            'CREATE TABLE job_runs(job_run_id uuid,job_key text,status text)',
            'CREATE TABLE paper_effects(effect_type text,entity_id uuid,job_run_id uuid)',
            'CREATE TABLE paper_portfolio_fill_applications(portfolio_id uuid,fill_id uuid)',
            'CREATE TABLE paper_portfolio_pnl_events(portfolio_id uuid,fill_id uuid)',
        ):
            connection.execute(text(statement))
        connection.execute(text("INSERT INTO instruments VALUES (:instrument,:exchange)"),
                           {"instrument": instrument, "exchange": "NSE" if market == "INDIA" else "NASDAQ"})
        connection.execute(text("INSERT INTO orders VALUES (:order,:instrument,'PAPER')"),
                           {"order": order, "instrument": instrument})
        connection.execute(text('INSERT INTO fills VALUES (:fill,:order)'), {'fill': fill, 'order': order})

    def append(job_key, copies=1, *, status='CLAIMED'):
        job = uuid4()
        with postgres_engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            if job_key is not None:
                connection.execute(text('INSERT INTO job_runs VALUES (:job,:key,:status)'), {'job': job, 'key': job_key, 'status': status})
            connection.execute(text("INSERT INTO paper_effects VALUES ('FILL',:fill,:job)"),
                               [{'fill': fill, 'job': job} for _ in range(copies)])

    def alter_instrument(*, exchange=None, missing=False):
        with postgres_engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            if missing:
                connection.execute(text('DELETE FROM instruments WHERE instrument_id=:id'), {'id': instrument})
            else:
                connection.execute(text('UPDATE instruments SET exchange=:exchange WHERE instrument_id=:id'),
                                   {'exchange': exchange, 'id': instrument})

    def observe():
        with postgres_engine.connect() as connection:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.commit()
            connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            generated = connection.execute(text('SELECT CURRENT_TIMESTAMP')).scalar_one()
            counts = 'SELECT ' + ','.join(f'(SELECT count(*) FROM {table})' for table in
                ('paper_effects', 'fills', 'orders', 'instruments', 'paper_portfolio_fill_applications', 'paper_portfolio_pnl_events'))
            before = connection.execute(text(counts)).one()
            first = _optional_projections(connection, generated)['execution_ledger']
            assert _optional_projections(connection, generated)['execution_ledger'] == first
            assert connection.execute(text('SHOW transaction_read_only')).scalar_one() == 'on'
            assert connection.execute(text(counts)).one() == before
            connection.rollback()
            connection.execute(text('RESET search_path'))
            connection.commit()
            return first
    try:
        yield append, observe, alter_instrument
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.mark.parametrize('job_key', [None, 'research:synthetic', 'PAPER:USA:synthetic', 'paperx:INDIA:synthetic'])
@pytest.mark.parametrize('copies', [1, 3])
def test_missing_or_wrong_namespace_receipts_never_authenticate_fill(legacy_receipt_case, job_key, copies):
    append, observe, _ = legacy_receipt_case
    append(job_key, copies)
    value = observe()
    assert value['status'] == 'AVAILABLE'
    assert value['fills'] == value['fills_without_effect_receipt'] == 1
    assert value['effect_receipt_fills'] == 0
    assert value['unattributed_effect_receipt_fills'] == 1
    assert value['reconciliation_state'] == 'UNATTRIBUTED_EFFECT_RECEIPTS'
    assert value['applied_fills'] == value['accounting_receipts'] == 0


@pytest.mark.parametrize('market', ['INDIA', 'USA'])
@pytest.mark.parametrize('status', ['CLAIMED', 'SUCCEEDED', 'FAILED'])
@pytest.mark.parametrize('copies', [1, 3])
def test_duplicate_legacy_receipts_do_not_inflate_attributed_fills(legacy_receipt_case, market, status, copies):
    append, observe, _ = legacy_receipt_case
    append(f'paper:{market}:synthetic', copies, status=status)
    value = observe()
    assert value['fills'] == value['effect_receipt_fills'] == 1
    assert value['fills_without_effect_receipt'] == value['unattributed_effect_receipt_fills'] == 0
    assert value['reconciliation_state'] == status+'_EFFECT_RECEIPTS_NOT_APPLIED'
    for owner in ('CLAIMED', 'SUCCEEDED', 'FAILED'):
        assert value[owner.lower()+'_effect_receipt_fills'] == int(owner == status)
        assert value[owner.lower()+'_effect_receipt_fills_without_application'] == int(owner == status)
    assert value['effect_receipt_fills_without_application'] == 1
    assert value['fill_applications'] == value['accounting_receipts'] == 0


@pytest.mark.parametrize('market', ['INDIA', 'USA'])
def test_conflicting_attributed_and_unattributed_lineage_is_refused(legacy_receipt_case, market):
    append, observe, _ = legacy_receipt_case
    append(f'paper:{market}:synthetic')
    append('research:conflicting')
    with pytest.raises(ValueError, match='HOPE_DASHBOARD_LEDGER_COUNT_INCONSISTENT'):
        observe()


@pytest.mark.parametrize('market', ['INDIA', 'USA'])
@pytest.mark.parametrize('status', [None, 'RUNNING', 'SUCCEEDED_UNVERIFIED'])
def test_unknown_receipt_owner_status_is_refused(legacy_receipt_case, market, status):
    append, observe, _ = legacy_receipt_case
    append(f'paper:{market}:synthetic', status=status)
    with pytest.raises(ValueError, match='HOPE_DASHBOARD_LEDGER_COUNT_INCONSISTENT'):
        observe()


@pytest.mark.parametrize('market', ['INDIA', 'USA'])
@pytest.mark.parametrize('pair', [('CLAIMED', 'SUCCEEDED'), ('SUCCEEDED', 'FAILED')])
def test_conflicting_paper_owner_statuses_cannot_double_classify_fill(legacy_receipt_case, market, pair):
    append, observe, _ = legacy_receipt_case
    for status in pair:
        append(f'paper:{market}:conflicting', status=status)
    with pytest.raises(ValueError, match='HOPE_DASHBOARD_LEDGER_COUNT_INCONSISTENT'):
        observe()


@pytest.mark.parametrize('market', ['INDIA', 'USA'])
@pytest.mark.parametrize('receipt_market', ['INDIA', 'USA', 'OTHER'])
def test_receipt_market_must_match_canonical_instrument(legacy_receipt_case, market, receipt_market):
    append, observe, _ = legacy_receipt_case
    append(f'paper:{receipt_market}:synthetic')
    value = observe()
    assert value['fills'] == value['effect_receipt_fills'] == 1
    assert value['fills_by_market'] == {m: int(m == market) for m in ('INDIA', 'USA', 'OTHER')}
    assert value['unapplied_fills_by_market'] == value['fills_by_market']
    assert value['effect_receipt_market_mismatch_fills'] == int(market != receipt_market)
    assert value['reconciliation_state'] == ('EFFECT_RECEIPT_MARKET_MISMATCH' if market != receipt_market
                                             else 'CLAIMED_EFFECT_RECEIPTS_NOT_APPLIED')


@pytest.mark.parametrize('market', ['INDIA', 'USA'])
def test_missing_canonical_instrument_never_hides_persisted_paper_fill(legacy_receipt_case, market):
    append, observe, alter = legacy_receipt_case
    append(f'paper:{market}:synthetic')
    alter(missing=True)
    value = observe()
    assert value['fills'] == value['effect_receipt_fills'] == 1
    assert value['fills_by_market'] == value['unapplied_fills_by_market'] == {'INDIA': 0, 'USA': 0, 'OTHER': 1}
    assert value['effect_receipt_market_mismatch_fills'] == 1
    assert value['reconciliation_state'] == 'EFFECT_RECEIPT_MARKET_MISMATCH'


@pytest.mark.parametrize('market', ['INDIA', 'USA'])
@pytest.mark.parametrize('exchange', [None, 'UNKNOWN'])
def test_unknown_exchange_remains_explicitly_unresolved(legacy_receipt_case, market, exchange):
    append, observe, alter = legacy_receipt_case
    append(f'paper:{market}:synthetic')
    alter(exchange=exchange)
    value = observe()
    assert value['fills'] == 1
    assert value['fills_by_market'] == value['unapplied_fills_by_market'] == {'INDIA': 0, 'USA': 0, 'OTHER': 1}
    assert value['effect_receipt_market_mismatch_fills'] == 1
    assert value['reconciliation_state'] == 'EFFECT_RECEIPT_MARKET_MISMATCH'
