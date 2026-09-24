import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
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
def test_conflicting_persisted_durable_id_blocks_entire_due_batch() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 14, 50, tzinfo=UTC)
    earlier = create_scheduled_job_run(
        "paper-batch-before-durable-id-collision",
        now - timedelta(minutes=2),
    )
    conflicted = create_scheduled_job_run(
        "paper-batch-durable-id-collision",
        now - timedelta(minutes=1),
    )
    calls = []

    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("DELETE FROM job_runs WHERE job_run_id IN (:earlier_id, :conflicted_id)"),
            {
                "earlier_id": earlier.job_run_id,
                "conflicted_id": conflicted.job_run_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO job_runs(job_run_id, job_key, scheduled_for) "
                "VALUES (:job_run_id, :job_key, :scheduled_for)"
            ),
            {
                "job_run_id": conflicted.job_run_id,
                "job_key": "paper-conflicting-durable-id-owner",
                "scheduled_for": conflicted.scheduled_for - timedelta(minutes=10),
            },
        )

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                earlier.job_key,
                lambda runtime: calls.append(earlier.job_run_id),
            ),
            PaperJobDefinition(
                conflicted.job_key,
                lambda runtime: calls.append(conflicted.job_run_id),
            ),
        ]
    )

    with pytest.raises(ValueError, match="JOB_RUN_IDENTITY_CONFLICT"):
        run_due_operational_paper_jobs(
            engine,
            registry,
            [earlier, conflicted],
            now=lambda: now,
            max_lateness=timedelta(minutes=5),
        )

    assert calls == []
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.get_record(earlier.job_run_id) is None
        stored = connection.execute(
            text(
                "SELECT job_key, scheduled_for FROM job_runs "
                "WHERE job_run_id = :job_run_id"
            ),
            {"job_run_id": conflicted.job_run_id},
        ).mappings().one()
        assert stored["job_key"] == "paper-conflicting-durable-id-owner"
        assert stored["scheduled_for"] == conflicted.scheduled_for - timedelta(minutes=10)
