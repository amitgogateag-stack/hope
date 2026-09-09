import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


UTC = timezone.utc


@pytest.mark.integration
def test_job_run_terminal_transitions_are_atomic_and_idempotent() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    success_run = create_scheduled_job_run(
        "paper-cycle-success",
        datetime(2026, 9, 9, 18, 0, tzinfo=UTC),
    )
    failed_run = create_scheduled_job_run(
        "paper-cycle-failure",
        datetime(2026, 9, 9, 19, 0, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("DELETE FROM job_runs WHERE job_run_id IN (:success_id, :failed_id)"),
            {
                "success_id": success_run.job_run_id,
                "failed_id": failed_run.job_run_id,
            },
        )
        repository = SqlAlchemyJobRunRepository(connection)

        assert repository.claim(success_run) is True
        assert repository.claim(failed_run) is True

        claimed = repository.get_record(success_run.job_run_id)
        assert claimed is not None
        assert claimed.status is JobRunStatus.CLAIMED
        assert claimed.completed_at is None
        assert claimed.failure_code is None

        success = create_job_run_completion(
            success_run,
            JobRunStatus.SUCCEEDED,
            success_run.scheduled_for + timedelta(minutes=5),
        )
        conflicting_failure = create_job_run_completion(
            success_run,
            JobRunStatus.FAILED,
            success_run.scheduled_for + timedelta(minutes=6),
            failure_code="SHOULD_NOT_OVERWRITE_SUCCESS",
        )
        failure = create_job_run_completion(
            failed_run,
            JobRunStatus.FAILED,
            failed_run.scheduled_for + timedelta(minutes=3),
            failure_code="UPSTREAM_DATA_UNAVAILABLE",
        )

        assert repository.complete(success) is True
        assert repository.complete(success) is False
        assert repository.complete(conflicting_failure) is False
        assert repository.complete(failure) is True
        assert repository.complete(failure) is False

        success_record = repository.get_record(success_run.job_run_id)
        assert success_record is not None
        assert success_record.status is JobRunStatus.SUCCEEDED
        assert success_record.completed_at == success.completed_at
        assert success_record.failure_code is None

        failed_record = repository.get_record(failed_run.job_run_id)
        assert failed_record is not None
        assert failed_record.status is JobRunStatus.FAILED
        assert failed_record.completed_at == failure.completed_at
        assert failed_record.failure_code == "UPSTREAM_DATA_UNAVAILABLE"


@pytest.mark.integration
def test_job_run_completion_rejects_durable_identity_conflict() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    job_run = create_scheduled_job_run(
        "paper-cycle-corrupted-completion",
        datetime(2026, 9, 9, 20, 0, tzinfo=UTC),
    )
    completion = create_job_run_completion(
        job_run,
        JobRunStatus.SUCCEEDED,
        job_run.scheduled_for + timedelta(minutes=5),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "DELETE FROM job_runs "
                "WHERE job_run_id = :job_run_id "
                "OR (job_key = :job_key AND scheduled_for = :scheduled_for)"
            ),
            {
                "job_run_id": job_run.job_run_id,
                "job_key": job_run.job_key,
                "scheduled_for": job_run.scheduled_for,
            },
        )
        connection.execute(
            text(
                "INSERT INTO job_runs(job_run_id, job_key, scheduled_for) "
                "VALUES (:job_run_id, :corrupted_key, :scheduled_for)"
            ),
            {
                "job_run_id": job_run.job_run_id,
                "corrupted_key": "different-job-key",
                "scheduled_for": job_run.scheduled_for,
            },
        )
        repository = SqlAlchemyJobRunRepository(connection)

        with pytest.raises(ValueError, match="JOB_RUN_IDENTITY_CONFLICT"):
            repository.complete(completion)
