"""Stale transaction snapshots cannot authorize PAPER work or control writes."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.no_trade import PaperNoTradeWriter
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_no_trade import SqlAlchemyPaperNoTradeRepository
from hope.infrastructure.scheduling.paper_reconciliation import reconcile_completed_paper_run
from hope.infrastructure.scheduling.paper_reconciliation_batch import reconcile_completed_paper_batch
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository


@pytest.fixture
def control_database():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    with engine.begin() as connection:
        apply_migrations(connection, Path(__file__).parents[2] / "migrations")
        repository = SqlAlchemyPaperEnvironmentControlRepository(connection)
        if repository.current_state() == "HALTED":
            repository.transition("HALTED", "RUNNING", "snapshot test setup", actor="integration-test")
        strategy_id, version_id = uuid4(), uuid4()
        connection.execute(text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"),
            {"id": strategy_id, "name": f"SNAPSHOT_{strategy_id}"})
        connection.execute(text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:id,:strategy,'v1','snapshot-isolation-test')"),
            {"id": version_id, "strategy": strategy_id})
    try:
        yield engine, version_id
    finally:
        with engine.begin() as connection:
            connection.execute(text("UPDATE job_runs SET status='FAILED', completed_at=clock_timestamp(), failure_code='EXPECTED_CONTROL_SNAPSHOT_TEST_CLEANUP' WHERE status='CLAIMED' AND job_key LIKE :prefix"),
                {"prefix": f"paper:USA:{version_id}:%"})
            repository = SqlAlchemyPaperEnvironmentControlRepository(connection)
            if repository.current_state() == "HALTED":
                repository.transition("HALTED", "RUNNING", "snapshot test cleanup", actor="integration-test")
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
@pytest.mark.parametrize("entrypoint", ["assert_running", "direct_claim", "transition", "direct_transition", "reconcile_single", "reconcile_batch"])
def test_stale_control_snapshot_fails_closed(control_database, isolation, entrypoint):
    engine, version_id = control_database
    run = create_scheduled_job_run(f"paper:USA:{version_id}:snapshot-{uuid4()}",
        datetime(2026, 10, 1, 12, tzinfo=UTC))
    if entrypoint.startswith("reconcile"):
        with engine.begin() as connection:
            assert SqlAlchemyJobRunRepository(connection).claim(run)
            assert PaperNoTradeWriter(SqlAlchemyPaperNoTradeRepository(connection)).record(
                PaperCycleContext(run), strategy_version="snapshot-v1", decision_time=run.scheduled_for,
                inputs_hash="a" * 64)
    with engine.connect() as stale:
        transaction = stale.begin()
        stale.execute(text(f"SET TRANSACTION ISOLATION LEVEL {isolation}"))
        repository = SqlAlchemyPaperEnvironmentControlRepository(stale)
        assert repository.current_state() == "RUNNING"  # establish old snapshot without locking control
        with engine.begin() as operator:
            SqlAlchemyPaperEnvironmentControlRepository(operator).transition(
                "RUNNING", "HALTED", "peer halt after snapshot", actor="integration-test")
        try:
            with pytest.raises((RuntimeError, DBAPIError), match="READ_COMMITTED"):
                if entrypoint == "assert_running":
                    repository.assert_running()
                elif entrypoint == "reconcile_single":
                    reconcile_completed_paper_run(stale, run, current=run.scheduled_for)
                elif entrypoint == "reconcile_batch":
                    reconcile_completed_paper_batch(stale, (run,), current=run.scheduled_for)
                elif entrypoint == "transition":
                    repository.transition("RUNNING", "HALTED", "stale duplicate halt", actor="integration-test")
                elif entrypoint == "direct_transition":
                    stale.execute(text("INSERT INTO paper_environment_control_events(state,reason,actor) VALUES ('HALTED','stale direct duplicate halt','integration-test')"))
                else:
                    stale.execute(text("INSERT INTO job_runs(job_run_id,job_key,scheduled_for) VALUES (:id,:key,:scheduled)"),
                        {"id": run.job_run_id, "key": run.job_key, "scheduled": run.scheduled_for})
        finally:
            transaction.rollback()
    with engine.connect() as connection:
        assert SqlAlchemyPaperEnvironmentControlRepository(connection).current_state() == "HALTED"
        assert connection.execute(text("SELECT count(*) FROM job_runs WHERE job_key LIKE :prefix"),
            {"prefix": f"paper:USA:{version_id}:%"}).scalar_one() == (1 if entrypoint.startswith("reconcile") else 0)
        if entrypoint.startswith("reconcile"):
            assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run).status.value == "CLAIMED"
            assert connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
                {"id": str(run.job_run_id)}).scalar_one() == 0


@pytest.mark.integration
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_observational_control_read_and_generic_job_remain_available(control_database, isolation):
    engine, _ = control_database
    with engine.begin() as connection:
        connection.execute(text(f"SET TRANSACTION ISOLATION LEVEL {isolation}"))
        assert SqlAlchemyPaperEnvironmentControlRepository(connection).current_state() == "RUNNING"
        run = create_scheduled_job_run(f"generic-snapshot-{uuid4()}", datetime(2026, 10, 1, 12, tzinfo=UTC))
        assert SqlAlchemyJobRunRepository(connection).claim(run)


@pytest.mark.integration
def test_read_committed_waiter_observes_committed_halt(control_database):
    engine, _ = control_database
    ready = Event()
    waiter_pid = []
    with engine.connect() as operator:
        transaction = operator.begin()
        SqlAlchemyPaperEnvironmentControlRepository(operator).transition(
            "RUNNING", "HALTED", "uncommitted halt for waiter", actor="integration-test")
        def wait_for_control():
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout='10s'"))
                waiter_pid.append(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
                ready.set()
                with pytest.raises(RuntimeError, match="PAPER_ENVIRONMENT_HALTED"):
                    SqlAlchemyPaperEnvironmentControlRepository(connection).assert_running()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(wait_for_control)
                assert ready.wait(timeout=5)
                deadline = time.monotonic() + 5
                with engine.connect() as observer:
                    while True:
                        if observer.execute(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": waiter_pid[0]}).scalar_one():
                            break
                        assert time.monotonic() < deadline, "Control waiter did not block"
                        time.sleep(0.01)
                transaction.commit()
                future.result(timeout=10)
        finally:
            if transaction.is_active:
                transaction.rollback()


@pytest.mark.integration
def test_lost_halt_transaction_releases_control_without_committing_event(control_database):
    engine, _ = control_database
    with engine.connect() as observer:
        before = observer.execute(text("SELECT max(control_sequence) FROM paper_environment_control_events")).scalar_one()
    operator = engine.connect()
    transaction = operator.begin()
    backend = operator.execute(text("SELECT pg_backend_pid()")).scalar_one()
    try:
        SqlAlchemyPaperEnvironmentControlRepository(operator).transition(
            "RUNNING", "HALTED", "interrupted halt transaction", actor="integration-test")
        with engine.begin() as killer:
            assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend}).scalar_one()
        operator.invalidate()
    finally:
        transaction.rollback()
        operator.close()
    with engine.begin() as restarted:
        restarted.execute(text("SET LOCAL statement_timeout='10s'"))
        repository = SqlAlchemyPaperEnvironmentControlRepository(restarted)
        repository.assert_running()
        assert restarted.execute(text("SELECT max(control_sequence) FROM paper_environment_control_events")).scalar_one() == before
        repository.transition("RUNNING", "HALTED", "post-restart halt", actor="integration-test")
    with engine.connect() as verification:
        assert SqlAlchemyPaperEnvironmentControlRepository(verification).current_state() == "HALTED"
