"""Supported accounting cannot extend a portfolio with an interrupted owner."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.application.paper import PaperSignalWriter, PaperRiskWriter, PaperOrderWriter, PaperFillAccountingWriter
from hope.application.paper.context import PaperCycleContext
from hope.domain.signal.models import Signal, SignalType
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.execution import Environment, Order, OrderSide, Fill
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_fill_accounting import SqlAlchemyPaperFillAccountingRepository
from hope.infrastructure.scheduling import paper_reconciliation_batch as batch
from hope.infrastructure.scheduling.paper_reconciliation_batch import reconcile_completed_paper_batch

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _write_execution(connection, run, portfolio_id, instrument_id):
    context = PaperCycleContext(run)
    signal_id = context.signal_id(instrument_id=instrument_id, strategy_version="shared-v1",
        decision_time=run.scheduled_for, signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"), inputs_hash="a" * 64)
    signal = Signal(signal_id=signal_id, instrument_id=instrument_id, strategy_version="shared-v1",
        decision_time=run.scheduled_for, signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"), inputs_hash="a" * 64)
    order = Order(order_id=context.order_id(signal_id), signal_id=signal_id,
        instrument_id=instrument_id, side=OrderSide.BUY, quantity=Decimal("2"),
        environment=Environment.PAPER, signal_type=SignalType.ENTRY)
    fill = Fill(context.fill_id(signal_id, 0), order.order_id, signal_id, instrument_id,
        OrderSide.BUY, Decimal("2"), Decimal("100"), Decimal("0.50"), Decimal("0"),
        "shared-cost-v1", run.scheduled_for)
    assert PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    assert PaperRiskWriter(SqlAlchemyPaperRiskRepository(connection)).record(context,
        RiskAssessment(signal_id=signal_id, decision=RiskDecision.APPROVE,
            reason_code="TEST_APPROVED", approved_quantity=Decimal("2")))
    assert PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    assert PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection)).record(
        context, portfolio_id, Decimal("1000"), fill, sequence=0)


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["normal", "reverse", "receipt_failure", "crash", "concurrent"])
def test_shared_portfolio_requires_recovery_before_next_complete_claim(scenario, monkeypatch):
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    strategy_id, version_id, portfolio_id, instrument_id = (uuid4() for _ in range(4))
    # Choose valid deterministic schedule identities that put the successful
    # historical owner first in UUID lock order. This exposes proof order
    # independently of caller selection order without forging durable identity.
    first = min((create_scheduled_job_run(f"paper:USA:{version_id}:shared-first-{i}", NOW) for i in range(32)), key=lambda run: run.job_run_id.int)
    for suffix in range(4096):
        second = create_scheduled_job_run(f"paper:USA:{version_id}:shared-next-{suffix}", NOW + timedelta(minutes=1))
        if first.job_run_id.int < second.job_run_id.int:
            break
    else:
        pytest.fail("Unable to select ordered schedule identities")
    runs = (first, second)
    try:
        with engine.begin() as connection:
            apply_migrations(connection, Path(__file__).parents[2] / "migrations")
            connection.execute(text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"),
                {"id": strategy_id, "name": f"SHARED_{strategy_id}"})
            connection.execute(text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:id,:strategy,'v1','shared-recovery-test')"),
                {"id": version_id, "strategy": strategy_id})
            connection.execute(text("INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) VALUES (:id,:symbol,'TEST','ACTIVE')"),
                {"id": instrument_id, "symbol": f"SHARED-{instrument_id}"})
            for run in runs:
                assert SqlAlchemyJobRunRepository(connection).claim(run)
            _write_execution(connection, runs[0], portfolio_id, instrument_id)
        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="PAPER_PORTFOLIO_PNL_OWNER_NOT_RECOVERABLE"):
                with connection.begin_nested():
                    _write_execution(connection, runs[1], portfolio_id, instrument_id)
            assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
                {"id": runs[1].job_run_id}).scalar_one() == 0
            assert connection.execute(text("SELECT cash,version FROM paper_portfolios WHERE portfolio_id=:id"),
                {"id": portfolio_id}).one() == (Decimal("799.50"), 1)
            assert SqlAlchemyJobRunRepository(connection).get_record_for_run(runs[0]).status is JobRunStatus.CLAIMED
            # The incomplete second member must roll back the first member's
            # otherwise provable success, including its immutable receipt.
            with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_NOT_PROVEN"):
                reconcile_completed_paper_batch(connection, runs, current=NOW + timedelta(minutes=2))
            assert SqlAlchemyJobRunRepository(connection).get_record_for_run(runs[0]).status is JobRunStatus.CLAIMED
            assert connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
                {"id": str(runs[0].job_run_id)}).scalar_one() == 0
            assert len(reconcile_completed_paper_batch(connection, (runs[0],), current=NOW + timedelta(minutes=2)).reconciled_run_ids) == 1
        with engine.begin() as connection:
            _write_execution(connection, runs[1], portfolio_id, instrument_id)

        def assert_pending(connection):
            assert SqlAlchemyJobRunRepository(connection).get_record_for_run(runs[0]).status is JobRunStatus.SUCCEEDED
            assert SqlAlchemyJobRunRepository(connection).get_record_for_run(runs[1]).status is JobRunStatus.CLAIMED
            assert connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
                {"id": str(runs[1].job_run_id)}).scalar_one() == 0
            assert connection.execute(text("SELECT cash,version FROM paper_portfolios WHERE portfolio_id=:id"),
                {"id": portfolio_id}).one() == (Decimal("599.00"), 2)

        # A selection containing only the historical receipt must still fail
        # while its newer complete owner is omitted. Recovery never discovers
        # or grants authority to an unselected CLAIMED peer.
        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_LINEAGE_MISMATCH"):
                reconcile_completed_paper_batch(connection, (runs[0],), current=NOW + timedelta(minutes=2))
            assert_pending(connection)

        if scenario == "receipt_failure":
            original = batch._reconcile_completed_paper_run
            def fail_historical(connection, run, **kwargs):
                if run == runs[0]:
                    raise RuntimeError("INJECTED_HISTORICAL_PROOF_FAILURE")
                return original(connection, run, **kwargs)
            with engine.begin() as connection:
                with monkeypatch.context() as patch:
                    patch.setattr(batch, "_reconcile_completed_paper_run", fail_historical)
                    with pytest.raises(RuntimeError, match="INJECTED_HISTORICAL_PROOF_FAILURE"):
                        reconcile_completed_paper_batch(connection, runs, current=NOW + timedelta(minutes=2))
                assert_pending(connection)
        if scenario == "crash":
            connection = engine.connect()
            transaction = connection.begin()
            backend = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            try:
                result = reconcile_completed_paper_batch(connection, runs, current=NOW + timedelta(minutes=2))
                assert result.reconciled_run_ids == (runs[1].job_run_id,)
                with engine.begin() as killer:
                    assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend}).scalar_one()
                connection.invalidate()
            finally:
                transaction.rollback()
                connection.close()
            with engine.connect() as connection:
                assert_pending(connection)
        if scenario == "concurrent":
            barrier = Barrier(2)
            def recover(selection):
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL statement_timeout='15s'"))
                    barrier.wait(timeout=10)
                    return reconcile_completed_paper_batch(connection, selection, current=NOW + timedelta(minutes=2))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(recover, runs), pool.submit(recover, tuple(reversed(runs)))]
                results = [future.result(timeout=30) for future in futures]
            assert sorted(len(result.reconciled_run_ids) for result in results) == [0, 1]
        else:
            with engine.begin() as connection:
                result = reconcile_completed_paper_batch(connection,
                    tuple(reversed(runs)) if scenario == "reverse" else runs,
                    current=NOW + timedelta(minutes=2))
                assert result.reconciled_run_ids == (runs[1].job_run_id,)
                assert result.already_reconciled_run_ids == (runs[0].job_run_id,)
        with engine.begin() as connection:
            assert connection.execute(text("SELECT cash,version FROM paper_portfolios WHERE portfolio_id=:id"),
                {"id": portfolio_id}).one() == (Decimal("599.00"), 2)
            assert connection.execute(text("SELECT quantity,average_price,realized_pnl,total_commission FROM paper_portfolio_positions WHERE portfolio_id=:id"),
                {"id": portfolio_id}).one() == (Decimal("4"), Decimal("100"), Decimal("0"), Decimal("1.00"))
            assert connection.execute(text("SELECT count(*),sum(realized_pnl_delta),sum(commission_delta) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"),
                {"id": portfolio_id}).one() == (2, Decimal("0"), Decimal("1.00"))
            assert connection.execute(text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:id"),
                {"id": portfolio_id}).scalar_one() == 2
            for run in runs:
                assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run).status is JobRunStatus.SUCCEEDED
                assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
                    {"id": run.job_run_id}).scalar_one() == 5
                assert connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
                    {"id": str(run.job_run_id)}).scalar_one() == 1
            retry = reconcile_completed_paper_batch(connection, runs, current=NOW + timedelta(minutes=3))
            assert retry.reconciled_run_ids == ()
            assert set(retry.already_reconciled_run_ids) == {run.job_run_id for run in runs}
    finally:
        try:
            with engine.begin() as connection:
                repository = SqlAlchemyJobRunRepository(connection)
                for run in runs:
                    record = repository.get_record_for_run(run)
                    if record is not None and record.status is JobRunStatus.CLAIMED:
                        repository.complete(create_job_run_completion(
                            run, JobRunStatus.FAILED, NOW + timedelta(minutes=4),
                            failure_code="EXPECTED_SHARED_PORTFOLIO_TEST_CLEANUP",
                        ))
        finally:
            engine.dispose()
