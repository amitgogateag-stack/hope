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
def test_stranded_later_claim_blocks_entire_due_batch_before_work_executes() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    earlier = create_scheduled_job_run("paper-batch-earlier", now - timedelta(minutes=2))
    stranded = create_scheduled_job_run("paper-batch-stranded", now - timedelta(minutes=1))
    calls = []

    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (earlier, stranded):
            connection.execute(
                text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
                {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
            )
        assert SqlAlchemyJobRunRepository(connection).claim(stranded) is True

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(earlier.job_key, lambda runtime: calls.append(earlier.job_run_id)),
            PaperJobDefinition(stranded.job_key, lambda runtime: calls.append(stranded.job_run_id)),
        ]
    )

    with pytest.raises(RuntimeError, match="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"):
        run_due_operational_paper_jobs(
            engine,
            registry,
            [earlier, stranded],
            now=lambda: now,
            max_lateness=timedelta(minutes=5),
        )

    assert calls == []
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.get_record(earlier.job_run_id) is None
        assert repository.get_record(stranded.job_run_id) is not None
