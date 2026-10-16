"""Quarantine preserves recovery evidence across writers, retries and crashes."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.application.paper import PaperCycleOutcome, PaperCycleRunner, PaperEffectType, create_paper_effect
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.no_trade import PaperNoTradeWriter
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_no_trade import SqlAlchemyPaperNoTradeRepository
from hope.infrastructure.scheduling.paper_reconciliation import reconcile_completed_paper_run


NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _completion(run):
    return create_job_run_completion(run, JobRunStatus.FAILED, NOW + timedelta(minutes=1),
                                     failure_code="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM")


@pytest.fixture
def claimed(postgres_engine):
    with postgres_engine.begin() as connection:
        apply_migrations(connection, Path(__file__).parents[2] / "migrations")
        strategy_id, version_id = uuid4(), uuid4()
        connection.execute(text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"),
                           {"id": strategy_id, "name": f"QUARANTINE_{strategy_id}"})
        connection.execute(text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) "
                                "VALUES (:id,:strategy,'v1','quarantine-evidence-test')"),
                           {"id": version_id, "strategy": strategy_id})
        run = create_scheduled_job_run(f"paper:USA:{version_id}:quarantine", NOW)
        assert SqlAlchemyJobRunRepository(connection).claim(run)
    try:
        yield postgres_engine, run
    finally:
        with postgres_engine.begin() as connection:
            repository = SqlAlchemyJobRunRepository(connection)
            if repository.get_record_for_run(run).status is JobRunStatus.CLAIMED:
                repository.complete(create_job_run_completion(
                    run, JobRunStatus.FAILED, NOW + timedelta(minutes=2),
                    failure_code="EXPECTED_QUARANTINE_TEST_CLEANUP",
                ))


def _runner(connection):
    return PaperCycleRunner(SqlAlchemyJobRunRepository(connection), now=lambda: NOW + timedelta(minutes=1))


def _effect(run):
    return create_paper_effect(run, PaperEffectType.SIGNAL, uuid4(), "b" * 64)


def _assert_claimed(connection, run, count):
    assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run).status is JobRunStatus.CLAIMED
    assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
                              {"id": run.job_run_id}).scalar_one() == count


@pytest.mark.integration
def test_empty_quarantine_is_idempotent_after_restart(claimed):
    engine, run = claimed
    with engine.begin() as connection:
        assert _runner(connection).quarantine_incomplete_claim(run) is PaperCycleOutcome.QUARANTINED_INCOMPLETE
    with engine.begin() as restarted:
        assert _runner(restarted).quarantine_incomplete_claim(run) is PaperCycleOutcome.SKIPPED_TERMINAL
        assert SqlAlchemyJobRunRepository(restarted).get_record_for_run(run) == _completion(run)


@pytest.mark.integration
@pytest.mark.parametrize("evidence", ["partial", "complete"])
def test_quarantine_rejects_durable_evidence_and_preserves_recovery(claimed, evidence):
    engine, run = claimed
    with engine.begin() as connection:
        if evidence == "partial":
            assert SqlAlchemyPaperEffectRepository(connection).record(_effect(run))
        else:
            assert PaperNoTradeWriter(SqlAlchemyPaperNoTradeRepository(connection)).record(
                PaperCycleContext(run), strategy_version="quarantine-v1", decision_time=NOW, inputs_hash="a" * 64,
            )
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="QUARANTINE_REQUIRES_RECONCILIATION"):
            _runner(connection).quarantine_incomplete_claim(run)
        _assert_claimed(connection, run, 1)
        # Deliberately commit after catching: no rejected terminalization survives.
    with engine.begin() as connection:
        with pytest.raises(IntegrityError, match="QUARANTINE_REQUIRES_RECONCILIATION"):
            with connection.begin_nested():
                SqlAlchemyJobRunRepository(connection).complete(_completion(run))
        _assert_claimed(connection, run, 1)
        if evidence == "complete":
            assert reconcile_completed_paper_run(connection, run, current=NOW + timedelta(minutes=1))


@pytest.mark.integration
def test_failed_terminal_reread_is_rolled_back_even_when_caught(claimed, monkeypatch):
    engine, run = claimed
    with engine.begin() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        original = repository.get_record_for_run
        calls = 0

        def reread(selection):
            nonlocal calls
            calls += 1
            return original(selection) if calls == 1 else None

        with monkeypatch.context() as patch:
            patch.setattr(repository, "get_record_for_run", reread)
            with pytest.raises(RuntimeError, match="QUARANTINE_NOT_DURABLE"):
                repository.quarantine_empty_claim(_completion(run))
        _assert_claimed(connection, run, 0)
    with engine.connect() as connection:
        _assert_claimed(connection, run, 0)


@pytest.mark.integration
def test_identity_conflict_cannot_skip_a_terminal_run(claimed):
    engine, run = claimed
    with engine.begin() as connection:
        assert _runner(connection).quarantine_incomplete_claim(run) is PaperCycleOutcome.QUARANTINED_INCOMPLETE
        forged = copy(run)
        object.__setattr__(forged, "job_key", run.job_key + "-forged")
        with pytest.raises(ValueError, match="JOB_RUN_IDENTITY_CONFLICT"):
            _runner(connection).quarantine_incomplete_claim(forged)


@pytest.mark.integration
def test_two_quarantine_workers_have_exactly_one_transition(claimed):
    engine, run = claimed
    ready = Barrier(2)

    def worker():
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            ready.wait(timeout=10)
            return _runner(connection).quarantine_incomplete_claim(run)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        outcomes = [future.result(timeout=20) for future in futures]
    assert outcomes.count(PaperCycleOutcome.QUARANTINED_INCOMPLETE) == 1
    assert outcomes.count(PaperCycleOutcome.SKIPPED_TERMINAL) == 1


@pytest.mark.integration
def test_quarantine_winner_blocks_effect_writer_until_terminal_commit(claimed):
    engine, run = claimed
    with engine.connect() as quarantine, engine.connect() as writer:
        transaction = quarantine.begin()
        try:
            assert _runner(quarantine).quarantine_incomplete_claim(run) is PaperCycleOutcome.QUARANTINED_INCOMPLETE
            with pytest.raises(OperationalError, match="lock timeout"):
                with writer.begin():
                    writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    SqlAlchemyPaperEffectRepository(writer).record(_effect(run))
            transaction.commit()
            with writer.begin():
                with pytest.raises(IntegrityError, match="PAPER_EFFECT_REQUIRES_CLAIMED_JOB"):
                    with writer.begin_nested():
                        SqlAlchemyPaperEffectRepository(writer).record(_effect(run))
        finally:
            if transaction.is_active:
                transaction.rollback()


@pytest.mark.integration
def test_effect_winner_blocks_quarantine_then_preserves_claim(claimed):
    engine, run = claimed
    with engine.connect() as writer, engine.connect() as quarantine:
        transaction = writer.begin()
        try:
            assert SqlAlchemyPaperEffectRepository(writer).record(_effect(run))
            with quarantine.begin():
                quarantine.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(OperationalError, match="lock timeout"):
                    _runner(quarantine).quarantine_incomplete_claim(run)
                # Repository savepoint preserves the caller's transaction.
                assert quarantine.execute(text("SELECT 1")).scalar_one() == 1
            transaction.commit()
            with quarantine.begin():
                with pytest.raises(RuntimeError, match="QUARANTINE_REQUIRES_RECONCILIATION"):
                    _runner(quarantine).quarantine_incomplete_claim(run)
                _assert_claimed(quarantine, run, 1)
        finally:
            if transaction.is_active:
                transaction.rollback()


@pytest.mark.integration
def test_direct_quarantine_statement_rechecks_effect_after_lock_wait(claimed):
    """The trigger must see an effect newer than UPDATE's initial snapshot."""
    engine, run = claimed
    started = Event()
    backend = []

    def direct_worker():
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            backend.append(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
            started.set()
            with pytest.raises(IntegrityError, match="QUARANTINE_REQUIRES_RECONCILIATION"):
                with connection.begin_nested():
                    SqlAlchemyJobRunRepository(connection).complete(_completion(run))

    with engine.connect() as writer, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = writer.begin()
        try:
            writer_pid = writer.execute(text("SELECT pg_backend_pid()")).scalar_one()
            assert SqlAlchemyPaperEffectRepository(writer).record(_effect(run))
            pending = pool.submit(direct_worker)
            assert started.wait(timeout=5)
            deadline = monotonic() + 5
            while True:
                blockers = writer.execute(text("SELECT pg_blocking_pids(:id)"), {"id": backend[0]}).scalar_one()
                if writer_pid in blockers:
                    break
                assert monotonic() < deadline, "quarantine did not reach the held job lock"
                sleep(0.01)
            transaction.commit()
            pending.result(timeout=15)
        finally:
            if transaction.is_active:
                transaction.rollback()
    with engine.connect() as restarted:
        _assert_claimed(restarted, run, 1)


@pytest.mark.integration
def test_lost_quarantine_transaction_restarts_as_claimed(claimed):
    engine, run = claimed
    connection = engine.connect()
    transaction = connection.begin()
    backend = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
    try:
        assert _runner(connection).quarantine_incomplete_claim(run) is PaperCycleOutcome.QUARANTINED_INCOMPLETE
        with engine.begin() as killer:
            assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend}).scalar_one()
        connection.invalidate()  # Server has aborted the outer transaction.
    finally:
        transaction.rollback()
        connection.close()
    with engine.begin() as restarted:
        _assert_claimed(restarted, run, 0)
        assert _runner(restarted).quarantine_incomplete_claim(run) is PaperCycleOutcome.QUARANTINED_INCOMPLETE


@pytest.mark.integration
def test_aborted_effect_transaction_does_not_strand_empty_quarantine(claimed):
    engine, run = claimed
    with engine.connect() as writer:
        transaction = writer.begin()
        assert SqlAlchemyPaperEffectRepository(writer).record(_effect(run))
        transaction.rollback()
    with engine.begin() as restarted:
        _assert_claimed(restarted, run, 0)
        assert _runner(restarted).quarantine_incomplete_claim(run) is PaperCycleOutcome.QUARANTINED_INCOMPLETE


@pytest.mark.integration
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
@pytest.mark.parametrize("entrypoint", ["repository", "direct_sql"])
def test_stale_snapshot_cannot_hide_committed_effect_from_quarantine(claimed, isolation, entrypoint):
    engine, run = claimed
    with engine.connect() as quarantine:
        transaction = quarantine.begin()
        quarantine.execute(text(f"SET TRANSACTION ISOLATION LEVEL {isolation}"))
        assert SqlAlchemyJobRunRepository(quarantine).get_record_for_run(run).status is JobRunStatus.CLAIMED
        with engine.begin() as writer:
            assert SqlAlchemyPaperEffectRepository(writer).record(_effect(run))
        try:
            with pytest.raises((RuntimeError, IntegrityError), match="QUARANTINE_REQUIRES_READ_COMMITTED"):
                with quarantine.begin_nested():
                    if entrypoint == "repository":
                        _runner(quarantine).quarantine_incomplete_claim(run)
                    else:
                        quarantine.execute(text("UPDATE job_runs SET status='FAILED', "
                                                "failure_code='PAPER_JOB_INCOMPLETE_PRIOR_CLAIM', "
                                                "completed_at=:completed WHERE job_run_id=:id"),
                                           {"id": run.job_run_id, "completed": NOW + timedelta(minutes=1)})
        finally:
            transaction.rollback()
    with engine.connect() as connection:
        _assert_claimed(connection, run, 1)


@pytest.mark.integration
def test_missing_claim_cannot_be_created_by_quarantine(postgres_engine):
    run = create_scheduled_job_run(f"paper-missing-quarantine-{uuid4()}", NOW)
    with postgres_engine.begin() as connection:
        apply_migrations(connection, Path(__file__).parents[2] / "migrations")
        with pytest.raises(RuntimeError, match="PAPER_JOB_CLAIM_STATE_MISSING"):
            _runner(connection).quarantine_incomplete_claim(run)
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run) is None


@pytest.mark.integration
def test_rejected_quarantine_preserves_cash_positions_and_pnl(claimed):
    from decimal import Decimal
    from hope.application.paper import PaperSignalWriter, PaperRiskWriter, PaperOrderWriter, PaperFillAccountingWriter
    from hope.domain.signal.models import Signal, SignalType
    from hope.domain.risk.models import RiskAssessment, RiskDecision
    from hope.domain.execution import Environment, Order, OrderSide, Fill
    from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
    from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
    from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
    from hope.infrastructure.repositories.paper_fill_accounting import SqlAlchemyPaperFillAccountingRepository

    engine, run = claimed
    portfolio_id, instrument_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) "
                                "VALUES (:id,:symbol,'TEST','ACTIVE')"),
                           {"id": instrument_id, "symbol": f"Q-{instrument_id}"})
        context = PaperCycleContext(run)
        signal_id = context.signal_id(instrument_id=instrument_id, strategy_version="quarantine-v1",
                                      decision_time=NOW, signal_type=SignalType.ENTRY,
                                      conviction=Decimal('0.8'), inputs_hash='a' * 64)
        signal = Signal(signal_id=signal_id, instrument_id=instrument_id, strategy_version="quarantine-v1",
                        decision_time=NOW, signal_type=SignalType.ENTRY, conviction=Decimal('0.8'), inputs_hash='a' * 64)
        order = Order(order_id=context.order_id(signal_id), signal_id=signal_id, instrument_id=instrument_id,
                      side=OrderSide.BUY, quantity=Decimal('2'), environment=Environment.PAPER, signal_type=SignalType.ENTRY)
        fill = Fill(context.fill_id(signal_id, 0), order.order_id, signal_id, instrument_id,
                    OrderSide.BUY, Decimal('2'), Decimal('100'), Decimal('0.50'),
                    Decimal('0'), 'quarantine-cost-v1', NOW)
        assert PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
        assert PaperRiskWriter(SqlAlchemyPaperRiskRepository(connection)).record(
            context, RiskAssessment(signal_id=signal_id, decision=RiskDecision.APPROVE,
                                    reason_code='TEST_APPROVED', approved_quantity=Decimal('2')),
        )
        assert PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
        assert PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection)).record(
            context, portfolio_id, Decimal('1000'), fill, sequence=0,
        )
    with engine.begin() as connection:
        before = connection.execute(text("SELECT cash,version FROM paper_portfolios WHERE portfolio_id=:id"),
                                    {"id": portfolio_id}).one()
        assert before == (Decimal('799.50'), 1)
        with pytest.raises(RuntimeError, match="QUARANTINE_REQUIRES_RECONCILIATION"):
            _runner(connection).quarantine_incomplete_claim(run)
        _assert_claimed(connection, run, 5)
        assert reconcile_completed_paper_run(connection, run, current=NOW + timedelta(minutes=1))
        assert connection.execute(text("SELECT cash,version FROM paper_portfolios WHERE portfolio_id=:id"),
                                  {"id": portfolio_id}).one() == before
        assert connection.execute(text("SELECT quantity,average_price,realized_pnl,total_commission "
                                       "FROM paper_portfolio_positions WHERE portfolio_id=:id"),
                                  {"id": portfolio_id}).one() == (Decimal('2'), Decimal('100'), Decimal('0'), Decimal('0.50'))
        assert connection.execute(text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"),
                                  {"id": portfolio_id}).scalar_one() == 1
