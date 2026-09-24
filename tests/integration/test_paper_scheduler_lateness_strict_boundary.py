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


@pytest.mark.integration
def test_run_one_microsecond_beyond_max_lateness_fails_before_claim() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    now = datetime(2026, 9, 10, 14, 45, tzinfo=UTC)
    stale = create_scheduled_job_run(
        "paper-batch-beyond-lateness-boundary",
        now - timedelta(minutes=5, microseconds=1),
    )
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
            {"job_key": stale.job_key, "scheduled_for": stale.scheduled_for},
        )
    registry = PaperJobRegistry(
        [PaperJobDefinition(stale.job_key, lambda runtime: calls.append(stale.job_run_id))]
    )
    with pytest.raises(RuntimeError, match="PAPER_SCHEDULER_RUN_STALE"):
        run_due_operational_paper_jobs(
            engine,
            registry,
            [stale],
            now=lambda: now,
            max_lateness=timedelta(minutes=5),
        )
    assert calls == []
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record(stale.job_run_id) is None
