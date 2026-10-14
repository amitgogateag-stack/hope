import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.scheduling.paper import run_due_operational_paper_jobs


def _engine():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    return create_engine(url)


@pytest.mark.integration
def test_batch_restart_skips_failed_terminal_run_and_executes_remaining_due_work() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 15, 20, tzinfo=UTC)
    failing = create_scheduled_job_run(
        "paper-batch-restart-failing-first",
        now - timedelta(minutes=2),
    )
    remaining = create_scheduled_job_run(
        "paper-batch-restart-remaining",
        now - timedelta(minutes=1),
    )
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (failing, remaining):
            connection.execute(
                text(
                    "DELETE FROM job_runs "
                    "WHERE job_key = :job_key AND scheduled_for = :scheduled_for"
                ),
                {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
            )

    def fail_work(runtime) -> None:
        calls.append(failing.job_run_id)
        raise RuntimeError("EXPECTED_FIRST_PAPER_FAILURE")

    first_registry = PaperJobRegistry(
        [
            PaperJobDefinition(failing.job_key, fail_work),
            PaperJobDefinition(
                remaining.job_key,
                lambda runtime: calls.append(remaining.job_run_id),
            ),
        ]
    )

    with pytest.raises(RuntimeError, match="EXPECTED_FIRST_PAPER_FAILURE"):
        run_due_operational_paper_jobs(
            engine,
            first_registry,
            [remaining, failing],
            now=lambda: now,
            max_lateness=timedelta(minutes=5),
        )

    assert calls == [failing.job_run_id]
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        failed_record = repository.get_record(failing.job_run_id)
        assert failed_record is not None
        assert failed_record.status is JobRunStatus.FAILED
        assert failed_record.failure_code == "RuntimeError"
        assert repository.get_record(remaining.job_run_id) is None

    restart_registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                failing.job_key,
                lambda runtime: calls.append(failing.job_run_id),
            ),
            PaperJobDefinition(
                remaining.job_key,
                lambda runtime: calls.append(remaining.job_run_id),
            ),
        ]
    )
    results = run_due_operational_paper_jobs(
        engine,
        restart_registry,
        [remaining, failing],
        now=lambda: now,
        max_lateness=timedelta(minutes=5),
    )

    assert [run.job_run_id for run, _ in results] == [failing.job_run_id, remaining.job_run_id]
    assert [outcome.value for _, outcome in results] == ["SKIPPED_TERMINAL", "EXECUTED"]
    assert calls == [failing.job_run_id, remaining.job_run_id]
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        failed_record = repository.get_record(failing.job_run_id)
        remaining_record = repository.get_record(remaining.job_run_id)
        assert failed_record is not None
        assert failed_record.status is JobRunStatus.FAILED
        assert failed_record.failure_code == "RuntimeError"
        assert remaining_record is not None
        assert remaining_record.status is JobRunStatus.SUCCEEDED


@pytest.mark.integration
def test_batch_rechecks_lateness_after_prior_job_advances_clock() -> None:
    """Later PAPER work cannot start after its deadline passes during the batch."""
    from uuid import uuid4

    engine = _engine()
    initial = datetime(2026, 10, 14, 5, 0, tzinfo=UTC)
    clock = [initial]
    suffix = uuid4()
    first = create_scheduled_job_run(
        f"paper-batch-time-advance-first-{suffix}",
        initial - timedelta(minutes=4),
    )
    second = create_scheduled_job_run(
        f"paper-batch-time-advance-second-{suffix}",
        initial - timedelta(minutes=3),
    )
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    def first_work(runtime) -> None:
        calls.append(first.job_run_id)
        clock[0] = initial + timedelta(minutes=3)

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(first.job_key, first_work),
            PaperJobDefinition(second.job_key, lambda runtime: calls.append(second.job_run_id)),
        ]
    )
    with pytest.raises(RuntimeError, match="PAPER_SCHEDULER_RUN_STALE"):
        run_due_operational_paper_jobs(
            engine,
            registry,
            (first, second),
            now=lambda: clock[0],
            max_lateness=timedelta(minutes=5),
        )

    assert calls == [first.job_run_id]
    with engine.connect() as connection:
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.get_record(first.job_run_id).status is JobRunStatus.SUCCEEDED
        assert jobs.get_record(second.job_run_id) is None
    engine.dispose()


@pytest.mark.integration
def test_scheduler_rejects_job_that_expires_while_binding_durable_inputs() -> None:
    """The last deadline check must occur after expensive connection-bound preparation."""
    from uuid import uuid4

    engine = _engine()
    start = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
    clock = [start]
    job = create_scheduled_job_run(
        f"paper-batch-slow-bind-{uuid4()}",
        start - timedelta(minutes=4),
    )
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    class SlowDurableBinding:
        def bind(self, connection):
            # The initial scheduler preflight is timely, but PIT preparation
            # crosses the job's five-minute lateness deadline.
            clock[0] = start + timedelta(minutes=2)
            return lambda runtime: calls.append(job.job_run_id)

    registry = PaperJobRegistry([PaperJobDefinition(job.job_key, SlowDurableBinding())])
    with pytest.raises(RuntimeError, match="PAPER_SCHEDULER_RUN_STALE"):
        run_due_operational_paper_jobs(
            engine,
            registry,
            (job,),
            now=lambda: clock[0],
            max_lateness=timedelta(minutes=5),
        )

    assert calls == []
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record(job.job_run_id) is None
    engine.dispose()


@pytest.mark.integration
def test_scheduler_allows_binding_at_exact_lateness_deadline() -> None:
    """The boundary remains inclusive; only work beyond the deadline is rejected."""
    from uuid import uuid4

    engine = _engine()
    start = datetime(2026, 10, 14, 6, 10, tzinfo=UTC)
    clock = [start]
    job = create_scheduled_job_run(
        f"paper-batch-boundary-bind-{uuid4()}",
        start - timedelta(minutes=4),
    )
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    class BoundaryDurableBinding:
        def bind(self, connection):
            clock[0] = start + timedelta(minutes=1)
            return lambda runtime: calls.append(job.job_run_id)

    registry = PaperJobRegistry([PaperJobDefinition(job.job_key, BoundaryDurableBinding())])
    result = run_due_operational_paper_jobs(
        engine,
        registry,
        (job,),
        now=lambda: clock[0],
        max_lateness=timedelta(minutes=5),
    )

    assert calls == [job.job_run_id]
    assert result[0][1] is not None and result[0][1].value == "EXECUTED"
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record(job.job_run_id).status is JobRunStatus.SUCCEEDED
    engine.dispose()
