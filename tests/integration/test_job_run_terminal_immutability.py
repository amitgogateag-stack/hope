import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


UTC = timezone.utc


@pytest.mark.integration
def test_terminal_job_run_history_cannot_be_modified_or_deleted() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    job_run = create_scheduled_job_run(
        "terminal-job-immutability",
        datetime(2026, 9, 12, 18, 45, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.claim(job_run) is True
        completion = create_job_run_completion(
            job_run,
            JobRunStatus.SUCCEEDED,
            job_run.scheduled_for + timedelta(minutes=5),
        )
        assert repository.complete(completion) is True

        with pytest.raises(IntegrityError, match="JOB_RUN_TERMINAL_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE job_runs SET job_key=:job_key WHERE job_run_id=:job_run_id"),
                    {"job_run_id": job_run.job_run_id, "job_key": "rewritten-terminal-job"},
                )

        with pytest.raises(IntegrityError, match="JOB_RUN_TERMINAL_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM job_runs WHERE job_run_id=:job_run_id"),
                    {"job_run_id": job_run.job_run_id},
                )

        stored = repository.get_record(job_run.job_run_id)
        assert stored is not None
        assert stored.run == job_run
        assert stored.status is JobRunStatus.SUCCEEDED
        assert stored.completed_at == completion.completed_at
        assert stored.failure_code is None
