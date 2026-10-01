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
def test_autonomous_scheduler_restart_skips_committed_run_and_executes_only_remaining_work() -> None:
    """A fresh scheduler/runtime must not replay committed PAPER work after process restart."""
    engine = _engine()
    now = datetime(2026, 9, 10, 16, 0, tzinfo=UTC)
    committed = create_scheduled_job_run(
        "paper-cross-runtime-committed",
        now - timedelta(minutes=2),
    )
    remaining = create_scheduled_job_run(
        "paper-cross-runtime-remaining",
        now - timedelta(minutes=1),
    )
    first_process_calls = []
    restarted_process_calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (committed, remaining):
            connection.execute(
                text(
                    "DELETE FROM job_runs "
                    "WHERE job_key = :job_key AND scheduled_for = :scheduled_for"
                ),
                {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
            )

    # Process A commits the first autonomous cycle before disappearing.
    first_registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                committed.job_key,
                lambda runtime: first_process_calls.append(committed.job_run_id),
            )
        ]
    )
    first_results = run_due_operational_paper_jobs(
        engine,
        first_registry,
        [committed],
        now=lambda: now,
        max_lateness=timedelta(minutes=5),
    )
    assert [outcome.value for _, outcome in first_results] == ["EXECUTED"]
    assert first_process_calls == [committed.job_run_id]

    # Process B has a fresh registry/runtime and sees both due runs. The durable
    # terminal identity from process A must suppress replay of committed work.
    restart_registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                committed.job_key,
                lambda runtime: restarted_process_calls.append(committed.job_run_id),
            ),
            PaperJobDefinition(
                remaining.job_key,
                lambda runtime: restarted_process_calls.append(remaining.job_run_id),
            ),
        ]
    )
    restart_results = run_due_operational_paper_jobs(
        engine,
        restart_registry,
        [remaining, committed],
        now=lambda: now,
        max_lateness=timedelta(minutes=5),
    )

    assert [run.job_run_id for run, _ in restart_results] == [
        committed.job_run_id,
        remaining.job_run_id,
    ]
    assert [outcome.value for _, outcome in restart_results] == [
        "SKIPPED_TERMINAL",
        "EXECUTED",
    ]
    assert restarted_process_calls == [remaining.job_run_id]

    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        committed_record = repository.get_record(committed.job_run_id)
        remaining_record = repository.get_record(remaining.job_run_id)
        assert committed_record is not None
        assert committed_record.status is JobRunStatus.SUCCEEDED
        assert remaining_record is not None
        assert remaining_record.status is JobRunStatus.SUCCEEDED
        assert connection.execute(
            text(
                "SELECT count(*) FROM job_runs "
                "WHERE job_key=:job_key AND scheduled_for=:scheduled_for"
            ),
            {"job_key": committed.job_key, "scheduled_for": committed.scheduled_for},
        ).scalar_one() == 1
