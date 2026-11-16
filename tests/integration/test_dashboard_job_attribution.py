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
def legacy_receipt_case(postgres_engine):
    schema = 'dashboard_job_' + uuid4().hex
    order, fill = uuid4(), uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        for statement in (
            'CREATE TABLE orders(order_id uuid,environment text)',
            'CREATE TABLE fills(fill_id uuid,order_id uuid)',
            'CREATE TABLE job_runs(job_run_id uuid,job_key text)',
            'CREATE TABLE paper_effects(effect_type text,entity_id uuid,job_run_id uuid)',
            'CREATE TABLE paper_portfolio_fill_applications(portfolio_id uuid,fill_id uuid)',
            'CREATE TABLE paper_portfolio_pnl_events(portfolio_id uuid,fill_id uuid)',
        ):
            connection.execute(text(statement))
        connection.execute(text("INSERT INTO orders VALUES (:order,'PAPER')"), {'order': order})
        connection.execute(text('INSERT INTO fills VALUES (:fill,:order)'), {'fill': fill, 'order': order})

    def append(job_key, copies=1):
        job = uuid4()
        with postgres_engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            if job_key is not None:
                connection.execute(text('INSERT INTO job_runs VALUES (:job,:key)'), {'job': job, 'key': job_key})
            connection.execute(text("INSERT INTO paper_effects VALUES ('FILL',:fill,:job)"),
                               [{'fill': fill, 'job': job} for _ in range(copies)])

    def observe():
        with postgres_engine.connect() as connection:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.commit()
            connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            generated = connection.execute(text('SELECT CURRENT_TIMESTAMP')).scalar_one()
            before = connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one()
            first = _optional_projections(connection, generated)['execution_ledger']
            assert _optional_projections(connection, generated)['execution_ledger'] == first
            assert connection.execute(text('SHOW transaction_read_only')).scalar_one() == 'on'
            assert connection.execute(text('SELECT count(*) FROM paper_effects')).scalar_one() == before
            connection.rollback()
            connection.execute(text('RESET search_path'))
            connection.commit()
            return first
    try:
        yield append, observe
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.mark.parametrize('job_key', [None, 'research:synthetic', 'PAPER:USA:synthetic', 'paperx:INDIA:synthetic'])
@pytest.mark.parametrize('copies', [1, 3])
def test_missing_or_wrong_namespace_receipts_never_authenticate_fill(legacy_receipt_case, job_key, copies):
    append, observe = legacy_receipt_case
    append(job_key, copies)
    value = observe()
    assert value['status'] == 'AVAILABLE'
    assert value['fills'] == value['fills_without_effect_receipt'] == 1
    assert value['effect_receipt_fills'] == 0
    assert value['unattributed_effect_receipt_fills'] == 1
    assert value['reconciliation_state'] == 'UNATTRIBUTED_EFFECT_RECEIPTS'
    assert value['applied_fills'] == value['accounting_receipts'] == 0


@pytest.mark.parametrize('market', ['INDIA', 'USA'])
@pytest.mark.parametrize('copies', [1, 3])
def test_duplicate_legacy_receipts_do_not_inflate_attributed_fills(legacy_receipt_case, market, copies):
    append, observe = legacy_receipt_case
    append(f'paper:{market}:synthetic', copies)
    value = observe()
    assert value['fills'] == value['effect_receipt_fills'] == 1
    assert value['fills_without_effect_receipt'] == value['unattributed_effect_receipt_fills'] == 0
    assert value['reconciliation_state'] == 'EFFECT_RECEIPTS_NOT_APPLIED'
    assert value['effect_receipt_fills_without_application'] == 1
    assert value['fill_applications'] == value['accounting_receipts'] == 0


@pytest.mark.parametrize('market', ['INDIA', 'USA'])
def test_conflicting_attributed_and_unattributed_lineage_is_refused(legacy_receipt_case, market):
    append, observe = legacy_receipt_case
    append(f'paper:{market}:synthetic')
    append('research:conflicting')
    with pytest.raises(ValueError, match='HOPE_DASHBOARD_LEDGER_COUNT_INCONSISTENT'):
        observe()
