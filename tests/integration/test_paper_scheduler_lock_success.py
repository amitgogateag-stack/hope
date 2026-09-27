import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository
from hope.infrastructure.scheduling.paper import (
    _PAPER_SCHEDULER_LOCK_NAME,
    run_due_operational_paper_jobs,
)


def _engine():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    return create_engine(url)


def _ensure_resume_guard_strategy_version(connection) -> None:
    connection.execute(
        text(
            "INSERT INTO strategies(strategy_id, name, family) "
            "VALUES ("
            "'00000000-0000-0000-0000-000000000002', "
            "'PAPER_RESUME_GUARD_TEST', 'TEST'"
            ") ON CONFLICT DO NOTHING"
        )
    )
    connection.execute(
        text(
            "INSERT INTO strategy_versions("
            "strategy_version_id, strategy_id, version, code_commit"
            ") VALUES ("
            "'00000000-0000-0000-0000-000000000001', "
            "'00000000-0000-0000-0000-000000000002', "
            "'paper-resume-v1', 'paper-resume-test-commit'"
            ") ON CONFLICT DO NOTHING"
        )
    )


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


@pytest.mark.integration
def test_paper_scheduler_fails_closed_when_control_sequence_generator_is_invalid() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 15, 5, tzinfo=UTC)
    due = create_scheduled_job_run(
        "paper-batch-sequence-generator-health",
        now - timedelta(minutes=1),
    )
    migrations_dir = Path(__file__).parents[2] / "migrations"
    calls = []

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        control = SqlAlchemyPaperEnvironmentControlRepository(connection)
        if control.current_state() == "HALTED":
            control.transition(
                "HALTED",
                "RUNNING",
                "TEST_SEQUENCE_HEALTH_PREPARE_RUNNING",
                actor="TEST_OPERATOR",
            )
        history_max = connection.execute(
            text(
                "SELECT max(control_sequence) "
                "FROM paper_environment_control_events"
            )
        ).scalar_one()
        connection.execute(
            text(
                "SELECT setval("
                "'paper_environment_control_events_control_sequence_seq', "
                ":history_max, false)"
            ),
            {"history_max": history_max},
        )

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(
                due.job_key,
                lambda runtime: calls.append(due.job_run_id),
            )
        ]
    )

    try:
        with pytest.raises(
            RuntimeError,
            match="PAPER_ENVIRONMENT_CONTROL_SEQUENCE_GENERATOR_INVALID",
        ):
            run_due_operational_paper_jobs(
                engine,
                registry,
                [due],
                now=lambda: now,
                max_lateness=timedelta(minutes=5),
            )
        assert calls == []
        with engine.connect() as connection:
            assert SqlAlchemyJobRunRepository(connection).get_record(
                due.job_run_id
            ) is None
    finally:
        with engine.begin() as connection:
            history_max = connection.execute(
                text(
                    "SELECT max(control_sequence) "
                    "FROM paper_environment_control_events"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "SELECT setval("
                    "'paper_environment_control_events_control_sequence_seq', "
                    ":history_max, true)"
                ),
                {"history_max": history_max},
            )


@pytest.mark.integration
def test_halted_paper_environment_blocks_entire_batch_before_any_claim() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 15, 10, tzinfo=UTC)
    first = create_scheduled_job_run(
        "paper-batch-halt-first",
        now - timedelta(minutes=2),
    )
    second = create_scheduled_job_run(
        "paper-batch-halt-second",
        now - timedelta(minutes=1),
    )
    migrations_dir = Path(__file__).parents[2] / "migrations"
    calls = []

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        control = SqlAlchemyPaperEnvironmentControlRepository(connection)
        if control.current_state() == "HALTED":
            control.transition("HALTED", "RUNNING", "TEST_PREPARE_RUNNING", actor="TEST_OPERATOR")
        control.transition("RUNNING", "HALTED", "TEST_BATCH_GLOBAL_HALT", actor="TEST_OPERATOR")
        for run in (first, second):
            connection.execute(
                text(
                    "DELETE FROM job_runs "
                    "WHERE job_key = :job_key AND scheduled_for = :scheduled_for"
                ),
                {"job_key": run.job_key, "scheduled_for": run.scheduled_for},
            )

    registry = PaperJobRegistry(
        [
            PaperJobDefinition(first.job_key, lambda runtime: calls.append(first.job_run_id)),
            PaperJobDefinition(second.job_key, lambda runtime: calls.append(second.job_run_id)),
        ]
    )

    try:
        with pytest.raises(RuntimeError, match="PAPER_ENVIRONMENT_HALTED"):
            run_due_operational_paper_jobs(
                engine,
                registry,
                [first, second],
                now=lambda: now,
                max_lateness=timedelta(minutes=5),
            )

        assert calls == []
        with engine.connect() as connection:
            repository = SqlAlchemyJobRunRepository(connection)
            assert repository.get_record(first.job_run_id) is None
            assert repository.get_record(second.job_run_id) is None
    finally:
        with engine.begin() as connection:
            control = SqlAlchemyPaperEnvironmentControlRepository(connection)
            if control.current_state() == "HALTED":
                control.transition("HALTED", "RUNNING", "TEST_BATCH_GLOBAL_RESUME", actor="TEST_OPERATOR")


@pytest.mark.integration
def test_paper_environment_resume_fails_closed_with_incomplete_operational_claim() -> None:
    engine = _engine()
    migrations_dir = Path(__file__).parents[2] / "migrations"
    now = datetime(2026, 9, 10, 15, 20, tzinfo=UTC)
    claimed = create_scheduled_job_run(
        "paper:USA:00000000-0000-0000-0000-000000000001:resume-guard",
        now - timedelta(minutes=1),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        _ensure_resume_guard_strategy_version(connection)
        connection.execute(
            text(
                "DELETE FROM job_runs "
                "WHERE job_key = :job_key AND scheduled_for = :scheduled_for"
            ),
            {"job_key": claimed.job_key, "scheduled_for": claimed.scheduled_for},
        )
        control = SqlAlchemyPaperEnvironmentControlRepository(connection)
        if control.current_state() == "HALTED":
            control.transition(
                "HALTED",
                "RUNNING",
                "TEST_RESUME_GUARD_PREPARE_RUNNING",
                actor="TEST_OPERATOR",
            )

        repository = SqlAlchemyJobRunRepository(connection)
        assert repository.claim(claimed) is True
        control.transition(
            "RUNNING",
            "HALTED",
            "TEST_RESUME_GUARD_HALT",
            actor="TEST_OPERATOR",
        )

        with pytest.raises(
            RuntimeError,
            match="PAPER_ENVIRONMENT_RESUME_BLOCKED_BY_INCOMPLETE_CLAIM",
        ):
            control.transition(
                "HALTED",
                "RUNNING",
                "TEST_UNSAFE_RESUME",
                actor="TEST_OPERATOR",
            )

        repository.complete(
            create_job_run_completion(
                claimed,
                JobRunStatus.FAILED,
                now,
                failure_code="TEST_QUARANTINED",
            )
        )
        control.transition(
            "HALTED",
            "RUNNING",
            "TEST_SAFE_RESUME",
            actor="TEST_OPERATOR",
        )
        assert control.current_state() == "RUNNING"
