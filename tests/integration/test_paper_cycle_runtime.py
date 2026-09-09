import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.application.paper import PaperCycleOutcome, PaperCycleRunner
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


UTC = timezone.utc


@pytest.mark.integration
def test_paper_cycle_runtime_executes_once_and_persists_terminal_state() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    job_run = create_scheduled_job_run(
        "paper-runtime-cycle",
        datetime(2026, 9, 9, 20, 30, tzinfo=UTC),
    )
    calls = []

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
            {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
        )
        repository = SqlAlchemyJobRunRepository(connection)
        runner = PaperCycleRunner(
            repository,
            now=lambda: job_run.scheduled_for + timedelta(minutes=1),
        )

        assert runner.run(job_run, lambda run: calls.append(run.job_run_id)) is PaperCycleOutcome.EXECUTED
        assert runner.run(job_run, lambda run: pytest.fail("duplicate work must not run")) is PaperCycleOutcome.SKIPPED_TERMINAL
        assert calls == [job_run.job_run_id]

        record = repository.get_record(job_run.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.SUCCEEDED
        assert record.completed_at == job_run.scheduled_for + timedelta(minutes=1)
        assert record.failure_code is None
