"""Real PostgreSQL contention, transaction loss, and atomic receipt recovery."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.no_trade import PaperNoTradeWriter
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_no_trade import SqlAlchemyPaperNoTradeRepository
from hope.infrastructure.scheduling import paper_reconciliation_batch as batch


NOW = datetime(2026, 10, 1, 15, tzinfo=UTC)


@pytest.fixture
def durable_batch():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    runs = []
    with engine.begin() as connection:
        apply_migrations(connection, Path(__file__).parents[2] / "migrations")
        strategy_id, version_id = uuid4(), uuid4()
        connection.execute(text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"),
                           {"id": strategy_id, "name": f"BATCH_RECOVERY_{strategy_id}"})
        connection.execute(text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) "
                                "VALUES (:id,:strategy,'v1','batch-recovery-test')"),
                           {"id": version_id, "strategy": strategy_id})
        for index in range(2):
            run = create_scheduled_job_run(f"paper:USA:{version_id}:batch-{index}", NOW)
            assert SqlAlchemyJobRunRepository(connection).claim(run)
            assert PaperNoTradeWriter(SqlAlchemyPaperNoTradeRepository(connection)).record(
                PaperCycleContext(run), strategy_version="batch-v1", decision_time=NOW, inputs_hash="d" * 64,
            )
            runs.append(run)
    runs.sort(key=lambda r: r.job_run_id.int)
    try:
        yield engine, tuple(runs)
    finally:
        # Shared CI environment must never retain an interrupted claim.
        with engine.begin() as connection:
            repository = SqlAlchemyJobRunRepository(connection)
            for run in runs:
                record = repository.get_record_for_run(run)
                if record.status is JobRunStatus.CLAIMED:
                    repository.complete(create_job_run_completion(
                        run, JobRunStatus.FAILED, NOW + timedelta(minutes=2),
                        failure_code="EXPECTED_BATCH_RECOVERY_TEST",
                    ))
        engine.dispose()


def _assert_state(connection, runs, status, receipts):
    repository = SqlAlchemyJobRunRepository(connection)
    for run in runs:
        assert repository.get_record_for_run(run).status is status
        assert connection.execute(text("SELECT count(*) FROM audit_events "
                                       "WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
                                  {"id": str(run.job_run_id)}).scalar_one() == receipts
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
                                  {"id": run.job_run_id}).scalar_one() == 1


@pytest.mark.integration
def test_opposite_order_concurrent_batches_have_one_winner(durable_batch):
    engine, runs = durable_batch
    barrier = Barrier(2)

    def worker(selection):
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '15s'"))
            barrier.wait(timeout=10)
            return batch.reconcile_completed_paper_batch(connection, selection, current=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, runs), pool.submit(worker, tuple(reversed(runs)))]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(len(result.reconciled_run_ids) for result in results) == [0, 2]
    assert sorted(len(result.already_reconciled_run_ids) for result in results) == [0, 2]
    with engine.connect() as connection:
        _assert_state(connection, runs, JobRunStatus.SUCCEEDED, 1)


@pytest.mark.integration
def test_second_proof_failure_rolls_back_first_receipt_even_when_caught(durable_batch, monkeypatch):
    engine, runs = durable_batch
    original = batch._reconcile_completed_paper_run

    def fail_second(connection, run, **kwargs):
        if run == runs[1]:
            raise RuntimeError("INJECTED_SECOND_PROOF_FAILURE")
        return original(connection, run, **kwargs)

    with engine.begin() as connection:
        with monkeypatch.context() as patch:
            patch.setattr(batch, "_reconcile_completed_paper_run", fail_second)
            with pytest.raises(RuntimeError, match="INJECTED_SECOND_PROOF_FAILURE"):
                batch.reconcile_completed_paper_batch(connection, runs, current=NOW)
        _assert_state(connection, runs, JobRunStatus.CLAIMED, 0)
        # Commit the caller's transaction despite the caught error.
    with engine.begin() as connection:
        _assert_state(connection, runs, JobRunStatus.CLAIMED, 0)
        assert len(batch.reconcile_completed_paper_batch(connection, runs, current=NOW).reconciled_run_ids) == 2


@pytest.mark.integration
@pytest.mark.parametrize("crash_point", ["after_first_receipt", "after_batch_return"])
def test_backend_death_before_commit_leaves_no_partial_success(durable_batch, monkeypatch, crash_point):
    engine, runs = durable_batch
    original = batch._reconcile_completed_paper_run
    connection = engine.connect()
    transaction = connection.begin()
    backend = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()

    def kill_backend():
        with engine.begin() as killer:
            assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend}).scalar_one()

    def crash_after_first(connection, run, **kwargs):
        result = original(connection, run, **kwargs)
        if run == runs[0]:
            kill_backend()
        return result

    try:
        if crash_point == "after_first_receipt":
            with monkeypatch.context() as patch:
                patch.setattr(batch, "_reconcile_completed_paper_run", crash_after_first)
                with pytest.raises(RuntimeError, match="BATCH_DATABASE_UNAVAILABLE"):
                    batch.reconcile_completed_paper_batch(connection, runs, current=NOW)
        else:
            assert len(batch.reconcile_completed_paper_batch(connection, runs, current=NOW).reconciled_run_ids) == 2
            kill_backend()
    finally:
        try:
            transaction.rollback()
        except DBAPIError as exc:
            # The server, rather than the client, aborted this transaction.
            # Assert disconnect classification instead of hiding cleanup errors.
            assert exc.connection_invalidated
        connection.close()

    with engine.begin() as restarted:
        restarted.execute(text("SET LOCAL statement_timeout = '15s'"))
        _assert_state(restarted, runs, JobRunStatus.CLAIMED, 0)
        assert len(batch.reconcile_completed_paper_batch(restarted, reversed(runs), current=NOW).reconciled_run_ids) == 2
    with engine.connect() as restarted:
        _assert_state(restarted, runs, JobRunStatus.SUCCEEDED, 1)
