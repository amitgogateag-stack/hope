import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.scheduling.paper import (
    _PAPER_SCHEDULER_LOCK_NAME,
    run_due_operational_paper_jobs,
)


def _engine():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    return create_engine(url)


@pytest.mark.integration
def test_successful_paper_batch_releases_scheduler_lock() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 15, 0, tzinfo=UTC)
    due = create_scheduled_job_run(
        "paper-batch-lock-release-after-success",
        now - timedelta(minutes=1),
    )
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "DELETE FROM job_runs "
                "WHERE job_key = :job_key AND scheduled_for = :scheduled_for"
            ),
            {"job_key": due.job_key, "scheduled_for": due.scheduled_for},
        )

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                due.job_key,
                lambda runtime: calls.append(due.job_run_id),
            )
        ]
    )

    results = run_due_operational_paper_jobs(
        engine,
        registry,
        [due],
        now=lambda: now,
        max_lateness=timedelta(minutes=5),
    )

    assert [run.job_run_id for run, _ in results] == [due.job_run_id]
    assert [outcome.value for _, outcome in results] == ["EXECUTED"]
    assert calls == [due.job_run_id]
    with engine.connect() as connection:
        record = SqlAlchemyJobRunRepository(connection).get_record(due.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.SUCCEEDED

    with engine.connect() as probe_connection:
        assert probe_connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:lock_name)::bigint)"),
            {"lock_name": _PAPER_SCHEDULER_LOCK_NAME},
        ).scalar_one() is True
        assert probe_connection.execute(
            text("SELECT pg_advisory_unlock(hashtext(:lock_name)::bigint)"),
            {"lock_name": _PAPER_SCHEDULER_LOCK_NAME},
        ).scalar_one() is True
