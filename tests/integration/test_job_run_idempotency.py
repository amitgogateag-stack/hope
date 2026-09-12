import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


@pytest.mark.integration
def test_scheduled_job_run_claim_is_idempotent_in_postgres() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    job_run = create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
            {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
        )
        repository = SqlAlchemyJobRunRepository(connection)

        assert repository.claim(job_run) is True
        assert repository.claim(job_run) is False
        assert repository.get(job_run.job_run_id) == job_run
        count = connection.execute(
            text("SELECT count(*) FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
            {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
        ).scalar_one()
        assert count == 1


@pytest.mark.integration
def test_scheduled_job_run_claim_rejects_conflicting_durable_identity() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    job_run = create_scheduled_job_run(
        "paper-cycle-identity-conflict",
        datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
            {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
        )
        conflicting_id = uuid4()
        assert conflicting_id != job_run.job_run_id
        connection.execute(
            text(
                """
                INSERT INTO job_runs(job_run_id, job_key, scheduled_for)
                VALUES (:job_run_id, :job_key, :scheduled_for)
                """
            ),
            {
                "job_run_id": conflicting_id,
                "job_key": job_run.job_key,
                "scheduled_for": job_run.scheduled_for,
            },
        )

        repository = SqlAlchemyJobRunRepository(connection)
        with pytest.raises(ValueError, match="JOB_RUN_IDENTITY_CONFLICT"):
            repository.claim(job_run)

        stored_id = connection.execute(
            text("SELECT job_run_id FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
            {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
        ).scalar_one()
        assert stored_id == conflicting_id


@pytest.mark.integration
def test_claimed_job_run_identity_cannot_be_rewritten() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    job_run = create_scheduled_job_run(
        "paper-cycle-immutable-identity",
        datetime(2026, 9, 9, 21, 0, tzinfo=timezone.utc),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
            {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
        )
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.claim(job_run) is True

        with pytest.raises(IntegrityError, match="JOB_RUN_IDENTITY_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE job_runs SET job_key = :job_key WHERE job_run_id = :job_run_id"),
                    {"job_key": "rewritten-job-key", "job_run_id": job_run.job_run_id},
                )

        with pytest.raises(IntegrityError, match="JOB_RUN_IDENTITY_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE job_runs SET scheduled_for = :scheduled_for WHERE job_run_id = :job_run_id"),
                    {
                        "scheduled_for": datetime(2026, 9, 9, 22, 0, tzinfo=timezone.utc),
                        "job_run_id": job_run.job_run_id,
                    },
                )

        stored = connection.execute(
            text("SELECT job_run_id, job_key, scheduled_for, status FROM job_runs WHERE job_run_id = :job_run_id"),
            {"job_run_id": job_run.job_run_id},
        ).mappings().one()
        assert stored["job_run_id"] == job_run.job_run_id
        assert stored["job_key"] == job_run.job_key
        assert stored["scheduled_for"] == job_run.scheduled_for
        assert stored["status"] == "CLAIMED"
