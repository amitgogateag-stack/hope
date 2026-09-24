import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
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
def test_scheduler_lock_is_released_after_stale_preflight_failure() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 15, 0, tzinfo=UTC)
    stale = create_scheduled_job_run(
        "paper-batch-lock-release-after-stale-preflight",
        now - timedelta(hours=2),
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
            {
                "job_key": stale.job_key,
                "scheduled_for": stale.scheduled_for,
            },
        )

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                stale.job_key,
                lambda runtime: calls.append(stale.job_run_id),
            )
        ]
    )

    # Keep an independent PostgreSQL session open so successful acquisition below proves
    # the scheduler session released its advisory lock while unwinding the preflight error.
    with engine.connect() as probe_connection:
        with pytest.raises(RuntimeError, match="PAPER_SCHEDULER_RUN_STALE"):
            run_due_operational_paper_jobs(
                engine,
                registry,
                [stale],
                now=lambda: now,
                max_lateness=timedelta(minutes=5),
            )

        acquired = probe_connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:lock_name)::bigint)"),
            {"lock_name": _PAPER_SCHEDULER_LOCK_NAME},
        ).scalar_one()
        assert acquired is True
        assert probe_connection.execute(
            text("SELECT pg_advisory_unlock(hashtext(:lock_name)::bigint)"),
            {"lock_name": _PAPER_SCHEDULER_LOCK_NAME},
        ).scalar_one() is True

    assert calls == []
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record(stale.job_run_id) is None
