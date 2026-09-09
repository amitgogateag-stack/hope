import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

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
