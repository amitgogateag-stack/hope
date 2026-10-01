import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
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
def test_restart_rechecks_lateness_and_blocks_entire_batch_after_downtime() -> None:
    """A run that ages past max_lateness while the scheduler is down must never execute on restart."""
    engine = _engine()
    scheduled_for = datetime(2026, 9, 10, 16, 10, tzinfo=UTC)
    first_process_now = scheduled_for + timedelta(minutes=4)
    restart_now = scheduled_for + timedelta(minutes=6)
    aging_run = create_scheduled_job_run("paper-restart-aging-run", scheduled_for)
    fresh_run = create_scheduled_job_run(
        "paper-restart-fresh-run",
        restart_now - timedelta(minutes=1),
    )
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (aging_run, fresh_run):
            connection.execute(
                text(
                    "DELETE FROM job_runs "
                    "WHERE job_key = :job_key AND scheduled_for = :scheduled_for"
                ),
                {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
            )

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(aging_run.job_key, lambda runtime: calls.append(aging_run.job_run_id)),
            PaperJobDefinition(fresh_run.job_key, lambda runtime: calls.append(fresh_run.job_run_id)),
        ]
    )

    # Before downtime the run is still inside the permitted replay window. Do not execute it;
    # this models a scheduler process disappearing before it reaches the execution boundary.
    assert first_process_now - aging_run.scheduled_for <= timedelta(minutes=5)

    # A fresh scheduler process starts after enough downtime that the same durable run is stale.
    # Preflight must fail the whole due batch before either the stale or fresh run is claimed.
    with pytest.raises(RuntimeError, match="PAPER_SCHEDULER_RUN_STALE"):
        run_due_operational_paper_jobs(
            engine,
            registry,
            [fresh_run, aging_run],
            now=lambda: restart_now,
            max_lateness=timedelta(minutes=5),
        )

    assert calls == []
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.get_record(aging_run.job_run_id) is None
        assert repository.get_record(fresh_run.job_run_id) is None
        assert connection.execute(
            text(
                "SELECT count(*) FROM job_runs "
                "WHERE (job_key=:aging_key AND scheduled_for=:aging_time) "
                "OR (job_key=:fresh_key AND scheduled_for=:fresh_time)"
            ),
            {
                "aging_key": aging_run.job_key,
                "aging_time": aging_run.scheduled_for,
                "fresh_key": fresh_run.job_key,
                "fresh_time": fresh_run.scheduled_for,
            },
        ).scalar_one() == 0
