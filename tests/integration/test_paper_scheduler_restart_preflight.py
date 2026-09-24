import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
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


@pytest.mark.integration
def test_terminal_prior_run_does_not_block_fresh_due_run() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 14, 5, tzinfo=UTC)
    terminal = create_scheduled_job_run("paper-batch-terminal", now - timedelta(minutes=2))
    fresh = create_scheduled_job_run("paper-batch-fresh", now - timedelta(minutes=1))
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (terminal, fresh):
            connection.execute(text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"), {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for})
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.claim(terminal) is True
        assert repository.complete(create_job_run_completion(terminal, JobRunStatus.SUCCEEDED, terminal.scheduled_for + timedelta(seconds=30))) is True
    registry = PaperJobRegistry([PaperJobDefinition(terminal.job_key, lambda runtime: calls.append(terminal.job_run_id)), PaperJobDefinition(fresh.job_key, lambda runtime: calls.append(fresh.job_run_id))])
    results = run_due_operational_paper_jobs(engine, registry, [fresh, terminal], now=lambda: now, max_lateness=timedelta(minutes=5))
    assert [run.job_run_id for run, _ in results] == [terminal.job_run_id, fresh.job_run_id]
    assert [outcome.value for _, outcome in results] == ["SKIPPED_TERMINAL", "EXECUTED"]
    assert calls == [fresh.job_run_id]
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.get_record(terminal.job_run_id).status is JobRunStatus.SUCCEEDED
        assert repository.get_record(fresh.job_run_id).status is JobRunStatus.SUCCEEDED


@pytest.mark.integration
def test_failed_prior_run_does_not_block_fresh_due_run() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 14, 10, tzinfo=UTC)
    failed = create_scheduled_job_run("paper-batch-failed", now - timedelta(minutes=2))
    fresh = create_scheduled_job_run("paper-batch-fresh-after-failure", now - timedelta(minutes=1))
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (failed, fresh):
            connection.execute(text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"), {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for})
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.claim(failed) is True
        assert repository.complete(create_job_run_completion(failed, JobRunStatus.FAILED, failed.scheduled_for + timedelta(seconds=30), failure_code="EXPECTED_PAPER_FAILURE")) is True
    registry = PaperJobRegistry([PaperJobDefinition(failed.job_key, lambda runtime: calls.append(failed.job_run_id)), PaperJobDefinition(fresh.job_key, lambda runtime: calls.append(fresh.job_run_id))])
    results = run_due_operational_paper_jobs(engine, registry, [fresh, failed], now=lambda: now, max_lateness=timedelta(minutes=5))
    assert [run.job_run_id for run, _ in results] == [failed.job_run_id, fresh.job_run_id]
    assert [outcome.value for _, outcome in results] == ["SKIPPED_TERMINAL", "EXECUTED"]
    assert calls == [fresh.job_run_id]
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        failed_record = repository.get_record(failed.job_run_id)
        assert failed_record.status is JobRunStatus.FAILED
        assert failed_record.failure_code == "EXPECTED_PAPER_FAILURE"
        assert repository.get_record(fresh.job_run_id).status is JobRunStatus.SUCCEEDED


@pytest.mark.integration
def test_future_run_is_not_claimed_or_executed_during_restart_preflight() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 14, 15, tzinfo=UTC)
    due = create_scheduled_job_run("paper-batch-due-before-future", now - timedelta(minutes=1))
    future = create_scheduled_job_run("paper-batch-future", now + timedelta(minutes=1))
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (due, future):
            connection.execute(text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"), {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for})
    registry = PaperJobRegistry([PaperJobDefinition(due.job_key, lambda runtime: calls.append(due.job_run_id)), PaperJobDefinition(future.job_key, lambda runtime: calls.append(future.job_run_id))])
    results = run_due_operational_paper_jobs(engine, registry, [future, due], now=lambda: now, max_lateness=timedelta(minutes=5))
    assert [run.job_run_id for run, _ in results] == [due.job_run_id]
    assert [outcome.value for _, outcome in results] == ["EXECUTED"]
    assert calls == [due.job_run_id]
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.get_record(due.job_run_id).status is JobRunStatus.SUCCEEDED
        assert repository.get_record(future.job_run_id) is None


@pytest.mark.integration
def test_future_stranded_claim_does_not_block_current_due_work() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 14, 20, tzinfo=UTC)
    due = create_scheduled_job_run("paper-batch-due-with-future-claim", now - timedelta(minutes=1))
    future = create_scheduled_job_run("paper-batch-future-stranded", now + timedelta(minutes=1))
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (due, future):
            connection.execute(text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"), {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for})
        assert SqlAlchemyJobRunRepository(connection).claim(future) is True
    registry = PaperJobRegistry([PaperJobDefinition(due.job_key, lambda runtime: calls.append(due.job_run_id)), PaperJobDefinition(future.job_key, lambda runtime: calls.append(future.job_run_id))])
    results = run_due_operational_paper_jobs(engine, registry, [future, due], now=lambda: now, max_lateness=timedelta(minutes=5))
    assert [run.job_run_id for run, _ in results] == [due.job_run_id]
    assert [outcome.value for _, outcome in results] == ["EXECUTED"]
    assert calls == [due.job_run_id]
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.get_record(due.job_run_id).status is JobRunStatus.SUCCEEDED
        assert repository.get_record(future.job_run_id).status is JobRunStatus.CLAIMED


@pytest.mark.integration
def test_stale_terminal_history_does_not_block_fresh_due_work() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 14, 25, tzinfo=UTC)
    terminal = create_scheduled_job_run("paper-batch-stale-terminal", now - timedelta(hours=2))
    fresh = create_scheduled_job_run("paper-batch-fresh-after-stale-terminal", now - timedelta(minutes=1))
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (terminal, fresh):
            connection.execute(text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"), {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for})
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.claim(terminal) is True
        assert repository.complete(create_job_run_completion(terminal, JobRunStatus.SUCCEEDED, terminal.scheduled_for + timedelta(seconds=30))) is True
    registry = PaperJobRegistry([PaperJobDefinition(terminal.job_key, lambda runtime: calls.append(terminal.job_run_id)), PaperJobDefinition(fresh.job_key, lambda runtime: calls.append(fresh.job_run_id))])
    results = run_due_operational_paper_jobs(engine, registry, [fresh, terminal], now=lambda: now, max_lateness=timedelta(minutes=5))
    assert [run.job_run_id for run, _ in results] == [terminal.job_run_id, fresh.job_run_id]
    assert [outcome.value for _, outcome in results] == ["SKIPPED_TERMINAL", "EXECUTED"]
    assert calls == [fresh.job_run_id]
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.get_record(terminal.job_run_id).status is JobRunStatus.SUCCEEDED
        assert repository.get_record(fresh.job_run_id).status is JobRunStatus.SUCCEEDED


@pytest.mark.integration
def test_stale_failed_history_does_not_block_fresh_due_work() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
    failed = create_scheduled_job_run("paper-batch-stale-failed", now - timedelta(hours=2))
    fresh = create_scheduled_job_run("paper-batch-fresh-after-stale-failed", now - timedelta(minutes=1))
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (failed, fresh):
            connection.execute(text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"), {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for})
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.claim(failed) is True
        assert repository.complete(create_job_run_completion(failed, JobRunStatus.FAILED, failed.scheduled_for + timedelta(seconds=30), failure_code="EXPECTED_STALE_FAILURE")) is True
    registry = PaperJobRegistry([PaperJobDefinition(failed.job_key, lambda runtime: calls.append(failed.job_run_id)), PaperJobDefinition(fresh.job_key, lambda runtime: calls.append(fresh.job_run_id))])
    results = run_due_operational_paper_jobs(engine, registry, [fresh, failed], now=lambda: now, max_lateness=timedelta(minutes=5))
    assert [run.job_run_id for run, _ in results] == [failed.job_run_id, fresh.job_run_id]
    assert [outcome.value for _, outcome in results] == ["SKIPPED_TERMINAL", "EXECUTED"]
    assert calls == [fresh.job_run_id]
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        failed_record = repository.get_record(failed.job_run_id)
        fresh_record = repository.get_record(fresh.job_run_id)
        assert failed_record is not None
        assert failed_record.status is JobRunStatus.FAILED
        assert failed_record.failure_code == "EXPECTED_STALE_FAILURE"
        assert fresh_record is not None
        assert fresh_record.status is JobRunStatus.SUCCEEDED


@pytest.mark.integration
def test_stale_unclaimed_run_blocks_entire_due_batch_before_any_claim() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 14, 35, tzinfo=UTC)
    stale = create_scheduled_job_run("paper-batch-stale-unclaimed", now - timedelta(hours=2))
    fresh = create_scheduled_job_run("paper-batch-fresh-with-stale-unclaimed", now - timedelta(minutes=1))
    calls = []
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for job_run in (stale, fresh):
            connection.execute(
                text("DELETE FROM job_runs WHERE job_key = :job_key AND scheduled_for = :scheduled_for"),
                {"job_key": job_run.job_key, "scheduled_for": job_run.scheduled_for},
            )
    registry = PaperJobRegistry(
        [
            PaperJobDefinition(stale.job_key, lambda runtime: calls.append(stale.job_run_id)),
            PaperJobDefinition(fresh.job_key, lambda runtime: calls.append(fresh.job_run_id)),
        ]
    )
    with pytest.raises(RuntimeError, match="PAPER_SCHEDULER_RUN_STALE"):
        run_due_operational_paper_jobs(
            engine,
            registry,
            [fresh, stale],
            now=lambda: now,
            max_lateness=timedelta(minutes=5),
        )
    assert calls == []
    with engine.connect() as connection:
        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.get_record(stale.job_run_id) is None
        assert repository.get_record(fresh.job_run_id) is None
