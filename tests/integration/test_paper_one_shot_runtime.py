import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.application.paper import PaperCycleOutcome
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry, run_paper_once
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


UTC = timezone.utc


def _engine():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    return create_engine(url)


def _prepare_job(engine, job_run) -> None:
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
            {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
        )


@pytest.mark.integration
def test_run_paper_once_commits_successful_terminal_state() -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        "paper-one-shot-success",
        datetime(2026, 9, 10, 13, 30, tzinfo=UTC),
    )
    _prepare_job(engine, job_run)
    completed_at = job_run.scheduled_for + timedelta(minutes=1)
    calls = []
    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                job_run.job_key,
                lambda runtime: calls.append(runtime.cycle.job_run.job_run_id),
            )
        ]
    )

    outcome = run_paper_once(
        engine,
        job_run,
        registry,
        now=lambda: completed_at,
    )

    assert outcome is PaperCycleOutcome.EXECUTED
    assert calls == [job_run.job_run_id]
    with engine.connect() as connection:
        record = SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.SUCCEEDED
        assert record.completed_at == completed_at
        assert record.failure_code is None


@pytest.mark.integration
def test_run_paper_once_commits_failed_terminal_state_before_reraising() -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        "paper-one-shot-failure",
        datetime(2026, 9, 10, 13, 31, tzinfo=UTC),
    )
    _prepare_job(engine, job_run)
    completed_at = job_run.scheduled_for + timedelta(minutes=1)
    calls = []

    def fail(runtime) -> None:
        calls.append(runtime.cycle.job_run.job_run_id)
        raise ValueError("paper-one-shot-boom")

    registry = PaperJobRegistry([PaperJobDefinition(job_run.job_key, fail)])

    with pytest.raises(ValueError, match="paper-one-shot-boom"):
        run_paper_once(engine, job_run, registry, now=lambda: completed_at)

    assert calls == [job_run.job_run_id]
    with engine.connect() as connection:
        record = SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id)
        assert record is not None
        assert record.status is JobRunStatus.FAILED
        assert record.completed_at == completed_at
        assert record.failure_code == "ValueError"

    outcome = run_paper_once(
        engine,
        job_run,
        registry,
        now=lambda: completed_at + timedelta(minutes=1),
    )
    assert outcome is PaperCycleOutcome.SKIPPED_TERMINAL
    assert calls == [job_run.job_run_id]


@pytest.mark.integration
def test_run_paper_once_rejects_unregistered_job_before_lifecycle_claim() -> None:
    engine = _engine()
    job_run = create_scheduled_job_run(
        "paper-one-shot-unregistered",
        datetime(2026, 9, 10, 13, 32, tzinfo=UTC),
    )
    _prepare_job(engine, job_run)

    with pytest.raises(RuntimeError, match="PAPER_JOB_NOT_REGISTERED"):
        run_paper_once(
            engine,
            job_run,
            PaperJobRegistry([]),
            now=lambda: job_run.scheduled_for + timedelta(minutes=1),
        )

    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id) is None
