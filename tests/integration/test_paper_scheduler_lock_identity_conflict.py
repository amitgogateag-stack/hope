import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry
from hope.infrastructure.postgres.migrations import apply_migrations
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
def test_scheduler_lock_is_released_after_identity_conflict_preflight_failure() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 15, 10, tzinfo=UTC)
    due = create_scheduled_job_run(
        "paper-batch-lock-release-after-identity-conflict",
        now - timedelta(minutes=1),
    )
    conflicting_id = uuid4()
    assert conflicting_id != due.job_run_id
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "DELETE FROM job_runs "
                "WHERE job_key = :job_key AND scheduled_for = :scheduled_for"
            ),
            {
                "job_key": due.job_key,
                "scheduled_for": due.scheduled_for,
            },
        )
        connection.execute(
            text(
                "INSERT INTO job_runs(job_run_id, job_key, scheduled_for) "
                "VALUES (:job_run_id, :job_key, :scheduled_for)"
            ),
            {
                "job_run_id": conflicting_id,
                "job_key": due.job_key,
                "scheduled_for": due.scheduled_for,
            },
        )

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                due.job_key,
                lambda runtime: calls.append(due.job_run_id),
            )
        ]
    )

    # The identity conflict is raised while scheduler preflight owns the advisory lock.
    # Reacquisition from an independent PostgreSQL session proves that unwinding the
    # conflict path releases the scheduler lock rather than wedging later PAPER batches.
    with engine.connect() as probe_connection:
        with pytest.raises(ValueError, match="JOB_RUN_IDENTITY_CONFLICT"):
            run_due_operational_paper_jobs(
                engine,
                registry,
                [due],
                now=lambda: now,
                max_lateness=timedelta(minutes=5),
            )

        assert probe_connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:lock_name)::bigint)"),
            {"lock_name": _PAPER_SCHEDULER_LOCK_NAME},
        ).scalar_one() is True
        assert probe_connection.execute(
            text("SELECT pg_advisory_unlock(hashtext(:lock_name)::bigint)"),
            {"lock_name": _PAPER_SCHEDULER_LOCK_NAME},
        ).scalar_one() is True

    assert calls == []
    with engine.connect() as connection:
        stored_id = connection.execute(
            text(
                "SELECT job_run_id FROM job_runs "
                "WHERE job_key = :job_key AND scheduled_for = :scheduled_for"
            ),
            {
                "job_key": due.job_key,
                "scheduled_for": due.scheduled_for,
            },
        ).scalar_one()
        assert stored_id == conflicting_id
