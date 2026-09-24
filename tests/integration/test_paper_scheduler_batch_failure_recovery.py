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
