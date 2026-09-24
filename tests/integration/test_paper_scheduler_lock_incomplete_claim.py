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
def test_scheduler_lock_is_released_after_incomplete_claim_preflight_failure() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 15, 5, tzinfo=UTC)
    stranded = create_scheduled_job_run(
        "paper-batch-lock-release-after-incomplete-claim",
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
            {
                "job_key": stranded.job_key,
                "scheduled_for": stranded.scheduled_for,
            },
        )
        assert SqlAlchemyJobRunRepository(connection).claim(stranded) is True

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                stranded.job_key,
                lambda runtime: calls.append(stranded.job_run_id),
            )
        ]
    )

    # Keep an independent PostgreSQL session open so reacquisition proves that the
    # scheduler advisory lock is released while the stranded-claim preflight error unwinds.
    with engine.connect() as probe_connection:
        with pytest.raises(RuntimeError, match="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"):
            run_due_operational_paper_jobs(
                engine,
                registry,
                [stranded],
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
        record = SqlAlchemyJobRunRepository(connection).get_record(stranded.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.CLAIMED
