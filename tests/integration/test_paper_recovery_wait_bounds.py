"""Real PostgreSQL lock cancellation, savepoint recovery and connection reuse."""
import os
from time import monotonic

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from test_paper_reconciliation_batch_postgres import durable_batch, _assert_state, NOW
from hope.application.jobs import JobRunStatus
from hope.infrastructure.scheduling import paper_recovery_limits as limits
from hope.infrastructure.scheduling import paper_reconciliation_batch as batch
from hope.infrastructure.scheduling import certified_paper_recovery as certified


def settings(connection):
    return dict(connection.execute(text("SELECT name,setting FROM pg_settings "
        "WHERE name IN ('lock_timeout','statement_timeout')")).all())


@pytest.fixture
def database():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url, pool_size=1, max_overflow=0)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("lock,statement,expected", [
    ("0", "0", ("5000", "30000")),
    ("2min", "1min", ("5000", "30000")),
    ("41ms", "53ms", ("41", "53")),
])
def test_bounds_preserve_stricter_settings_and_restore_success(database, lock, statement, expected):
    with database.begin() as connection:
        initial = settings(connection)
        connection.execute(text("SELECT set_config('lock_timeout',:l,true), "
            "set_config('statement_timeout',:s,true)"), {"l":lock,"s":statement})
        caller = settings(connection)
        with limits.bounded_paper_recovery(connection):
            assert settings(connection) == dict(lock_timeout=expected[0], statement_timeout=expected[1])
            with limits.bounded_paper_recovery(connection):
                assert settings(connection) == dict(lock_timeout=expected[0], statement_timeout=expected[1])
        assert settings(connection) == caller
    with database.connect() as reused:
        assert settings(reused) == initial


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["proof", "interrupt", "statement"])
def test_savepoint_failure_restores_settings_and_preserves_caller_work(database, failure):
    with database.begin() as connection:
        connection.execute(text("CREATE TEMP TABLE recovery_wait_probe (value int) ON COMMIT DROP"))
        connection.execute(text("INSERT INTO recovery_wait_probe VALUES (1)"))
        connection.execute(text("SET LOCAL statement_timeout='60ms'"))
        caller = settings(connection)
        error = {"proof":RuntimeError,"interrupt":KeyboardInterrupt,"statement":DBAPIError}[failure]
        with pytest.raises(error):
            with limits.bounded_paper_recovery(connection):
                connection.execute(text("INSERT INTO recovery_wait_probe VALUES (2)"))
                if failure == "statement":
                    connection.execute(text("SELECT pg_sleep(0.3)"))
                elif failure == "interrupt":
                    raise KeyboardInterrupt()
                else:
                    raise RuntimeError("injected proof failure")
        assert settings(connection) == caller
        assert connection.execute(text("SELECT value FROM recovery_wait_probe")).scalars().all() == [1]
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


@pytest.mark.integration
@pytest.mark.parametrize("entry", ["ordinary", "certified"])
@pytest.mark.parametrize("block", ["control", "job"])
def test_peer_locks_timeout_without_partial_success_then_retry(durable_batch, monkeypatch, entry, block):
    engine, runs = durable_batch
    # Shorten only test duration; production cap values are asserted above.
    monkeypatch.setattr(limits, "RECOVERY_LOCK_TIMEOUT_MS", 80)
    with engine.begin() as blocker:
        if block == "control":
            blocker.execute(text("SELECT pg_advisory_xact_lock("
                "hashtext('hope:paper:environment-control')::bigint)"))
        else:
            blocker.execute(text("SELECT job_run_id FROM job_runs WHERE job_run_id=:id FOR UPDATE"),
                {"id":runs[0 if entry == "certified" else 1].job_run_id})
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout='3s'"))
            before = settings(connection)
            start = monotonic()
            with pytest.raises(RuntimeError, match="DATABASE_TIMEOUT") as caught:
                if entry == "ordinary":
                    batch.reconcile_completed_paper_batch(connection, runs, current=NOW)
                else:
                    certified.recover_certified_paper_batch(connection,
                        (r.job_run_id for r in runs), current=NOW)
            assert caught.value.__cause__.orig.sqlstate == "55P03"
            assert monotonic() - start < 2.5
            assert settings(connection) == before
            _assert_state(connection, runs, JobRunStatus.CLAIMED, 0)
    # Retry after the conflicting transaction releases its locks; exactly one
    # receipt per run, no economic re-execution, even after a certified attempt.
    with engine.begin() as connection:
        result = batch.reconcile_completed_paper_batch(connection, runs, current=NOW)
        assert len(result.reconciled_run_ids) == 2
    with engine.connect() as connection:
        _assert_state(connection, runs, JobRunStatus.SUCCEEDED, 1)


@pytest.mark.integration
def test_statement_timeout_after_first_receipt_rolls_back_entire_batch(durable_batch, monkeypatch):
    engine, runs = durable_batch
    original = batch._reconcile_completed_paper_run
    def timeout_second(connection, run, **kwargs):
        if run == runs[1]:
            connection.execute(text("SELECT pg_sleep(0.4)"))
        return original(connection, run, **kwargs)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='90ms'"))
        before = settings(connection)
        with monkeypatch.context() as patch:
            patch.setattr(batch, "_reconcile_completed_paper_run", timeout_second)
            with pytest.raises(RuntimeError, match="BATCH_DATABASE_TIMEOUT") as caught:
                batch.reconcile_completed_paper_batch(connection, runs, current=NOW)
            assert caught.value.__cause__.orig.sqlstate == "57014"
        _assert_state(connection, runs, JobRunStatus.CLAIMED, 0)
        assert settings(connection) == before
    with engine.begin() as connection:
        _assert_state(connection, runs, JobRunStatus.CLAIMED, 0)
        batch.reconcile_completed_paper_batch(connection, runs, current=NOW)
        _assert_state(connection, runs, JobRunStatus.SUCCEEDED, 1)
