import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from hope.application.jobs import create_scheduled_job_run
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_control import (
    SqlAlchemyPaperEnvironmentControlRepository,
)
from hope.infrastructure.scheduling.paper_reconciliation import (
    reconcile_completed_paper_run,
)


def _engine():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    return create_engine(url)


@pytest.mark.integration
def test_direct_reconciliation_cannot_cross_durable_paper_halt() -> None:
    engine = _engine()
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 10, 4, 21, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        f"paper:USA:{uuid4()}:halted-reconciliation",
        scheduled_for,
    )

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            control = SqlAlchemyPaperEnvironmentControlRepository(connection)
            if control.current_state() == "RUNNING":
                control.transition(
                    "RUNNING",
                    "HALTED",
                    "TEST_DIRECT_RECONCILIATION_HALT",
                    actor="TEST_OPERATOR",
                )
            assert control.current_state() == "HALTED"
            assert (
                SqlAlchemyJobRunRepository(connection).get_record(
                    job_run.job_run_id
                )
                is None
            )

            with pytest.raises(RuntimeError, match="PAPER_ENVIRONMENT_HALTED"):
                reconcile_completed_paper_run(
                    connection,
                    job_run,
                    current=scheduled_for + timedelta(minutes=1),
                )

            assert (
                SqlAlchemyJobRunRepository(connection).get_record(
                    job_run.job_run_id
                )
                is None
            )
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_unproven_reconciliation_rolls_back_even_when_caller_commits() -> None:
    """A stranded claimed run without complete effects must remain claimed."""
    from sqlalchemy import text

    engine = _engine()
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 10, 9, 14, 30, tzinfo=UTC)
    strategy_id, version_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        f"paper:USA:{version_id}:incomplete-recovery-rollback", scheduled_for,
    )
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO strategies(strategy_id, name, family) "
                 "VALUES (:id, :name, 'TEST')"),
            {"id": strategy_id, "name": f"RECON_ROLLBACK_{strategy_id}"},
        )
        connection.execute(
            text("INSERT INTO strategy_versions("
                 "strategy_version_id, strategy_id, version, code_commit"
                 ") VALUES (:id, :strategy_id, 'v1', 'recovery-rollback')"),
            {"id": version_id, "strategy_id": strategy_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)

    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_NOT_PROVEN"):
            reconcile_completed_paper_run(
                connection, run, current=scheduled_for + timedelta(minutes=1),
            )
        # Intentionally allow outer transaction to commit after catching failure.

    with engine.connect() as connection:
        from hope.application.jobs import JobRunStatus

        durable = SqlAlchemyJobRunRepository(connection).get_record_for_run(run)
        assert durable is not None
        assert durable.status is JobRunStatus.CLAIMED
        assert connection.execute(
            text("SELECT count(*) FROM audit_events "
                 "WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
            {"id": str(run.job_run_id)},
        ).scalar_one() == 0

    # Never leave a stranded CLAIMED run in the shared CI database: the
    # global PAPER resume guard correctly refuses to bypass such a claim.
    from hope.application.jobs import JobRunStatus, create_job_run_completion

    with engine.begin() as connection:
        assert SqlAlchemyJobRunRepository(connection).complete(
            create_job_run_completion(
                run,
                JobRunStatus.FAILED,
                scheduled_for + timedelta(minutes=2),
                failure_code="EXPECTED_INCOMPLETE_RECONCILIATION_TEST",
            )
        )
    engine.dispose()


@pytest.mark.integration
def test_reconciliation_rejects_failed_terminal_run_without_creating_audit_receipt() -> None:
    """FAILED runs are not retroactively promoted to successful recovery."""
    from sqlalchemy import text
    from hope.application.jobs import JobRunStatus, create_job_run_completion

    engine = _engine()
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 10, 10, 11, 0, tzinfo=UTC)
    strategy_id, version_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        f"paper:USA:{version_id}:failed-terminal-recovery", scheduled_for,
    )
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO strategies(strategy_id, name, family) "
                 "VALUES (:id, :name, 'TEST')"),
            {"id": strategy_id, "name": f"FAILED_TERMINAL_{strategy_id}"},
        )
        connection.execute(
            text("INSERT INTO strategy_versions("
                 "strategy_version_id, strategy_id, version, code_commit"
                 ") VALUES (:id, :strategy_id, 'v1', 'failed-terminal-recovery')"),
            {"id": version_id, "strategy_id": strategy_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run)
        assert jobs.complete(create_job_run_completion(
            run, JobRunStatus.FAILED, scheduled_for + timedelta(seconds=30),
            failure_code="EXPECTED_FAILED_TERMINAL_RECOVERY",
        ))

    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_NOT_CLAIMED"):
            reconcile_completed_paper_run(
                connection, run, current=scheduled_for + timedelta(minutes=1),
            )

    with engine.connect() as connection:
        durable = SqlAlchemyJobRunRepository(connection).get_record_for_run(run)
        assert durable is not None
        assert durable.status is JobRunStatus.FAILED
        assert durable.failure_code == "EXPECTED_FAILED_TERMINAL_RECOVERY"
        assert connection.execute(
            text("SELECT count(*) FROM audit_events "
                 "WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
            {"id": str(run.job_run_id)},
        ).scalar_one() == 0
    engine.dispose()


@pytest.mark.integration
def test_partial_durable_effects_cannot_be_reconciled_after_restart() -> None:
    """A committed SIGNAL alone is not proof of a completed execution."""
    from sqlalchemy import text
    from hope.application.jobs import JobRunStatus, create_job_run_completion
    from hope.application.paper.effects import PaperEffectType, create_paper_effect
    from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository

    engine = _engine()
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    strategy_id, version_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        f"paper:USA:{version_id}:partial-durable-recovery", scheduled_for,
    )
    effect = create_paper_effect(run, PaperEffectType.SIGNAL, uuid4(), "a" * 64)
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO strategies(strategy_id, name, family) "
                 "VALUES (:id, :name, 'TEST')"),
            {"id": strategy_id, "name": f"PARTIAL_RECON_{strategy_id}"},
        )
        connection.execute(
            text("INSERT INTO strategy_versions("
                 "strategy_version_id, strategy_id, version, code_commit"
                 ") VALUES (:id, :strategy_id, 'v1', 'partial-recovery')"),
            {"id": version_id, "strategy_id": strategy_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        assert SqlAlchemyPaperEffectRepository(connection).record(effect)

    # New connection simulates a restart; caller catches failure and commits.
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_NOT_PROVEN"):
            reconcile_completed_paper_run(
                connection, run, current=scheduled_for + timedelta(minutes=1),
            )

    with engine.begin() as connection:
        jobs = SqlAlchemyJobRunRepository(connection)
        durable = jobs.get_record_for_run(run)
        assert durable is not None and durable.status is JobRunStatus.CLAIMED
        assert SqlAlchemyPaperEffectRepository(connection).list_for_job_run(
            run.job_run_id,
        ) == (effect,)
        assert connection.execute(
            text("SELECT count(*) FROM audit_events "
                 "WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
            {"id": str(run.job_run_id)},
        ).scalar_one() == 0
        # Avoid leaving a CLAIMED fixture that would block global PAPER resume.
        assert jobs.complete(create_job_run_completion(
            run, JobRunStatus.FAILED, scheduled_for + timedelta(minutes=2),
            failure_code="EXPECTED_PARTIAL_RECONCILIATION_TEST",
        ))
    engine.dispose()


@pytest.mark.integration
def test_reconciliation_audit_failure_rolls_back_terminal_job_across_restart(
    monkeypatch,
) -> None:
    """A caught audit failure cannot commit a SUCCEEDED transition."""
    from sqlalchemy import text
    from hope.application.jobs import JobRunStatus, create_job_run_completion
    from hope.infrastructure.scheduling import paper_reconciliation
    from hope.infrastructure.scheduling.recovery import PaperRecoveryDecision

    engine = _engine()
    scheduled_for = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    strategy_id, version_id = uuid4(), uuid4()
    run = create_scheduled_job_run(
        f"paper:USA:{version_id}:audit-transition-rollback", scheduled_for,
    )
    with engine.begin() as connection:
        apply_migrations(connection, Path(__file__).parents[2] / "migrations")
        connection.execute(
            text("INSERT INTO strategies(strategy_id, name, family) "
                 "VALUES (:id, :name, 'TEST')"),
            {"id": strategy_id, "name": f"AUDIT_ROLLBACK_{strategy_id}"},
        )
        connection.execute(
            text("INSERT INTO strategy_versions("
                 "strategy_version_id, strategy_id, version, code_commit"
                 ") VALUES (:id, :strategy_id, 'v1', 'audit-rollback')"),
            {"id": version_id, "strategy_id": strategy_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)

    # Isolate the post-transition audit failure, not lineage validation:
    # the real PostgreSQL job transition and savepoint remain in use.
    monkeypatch.setattr(
        paper_reconciliation, "assess_due_paper_recovery",
        lambda *args, **kwargs: type("Report", (), {
            "assessments": (type("Assessment", (), {
                "job_run_id": run.job_run_id,
                "decision": PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS,
            })(),),
        })(),
    )
    monkeypatch.setattr(
        paper_reconciliation.SqlAlchemyPaperReconciliationAuditRepository,
        "record", lambda *args: False,
    )
    with engine.begin() as connection:
        with pytest.raises(
            RuntimeError, match="PAPER_JOB_RECONCILIATION_AUDIT_NOT_RECORDED",
        ):
            reconcile_completed_paper_run(
                connection, run, current=scheduled_for + timedelta(minutes=1),
            )
        # Deliberately commit the outer transaction despite caught failure.

    with engine.begin() as connection:
        jobs = SqlAlchemyJobRunRepository(connection)
        record = jobs.get_record_for_run(run)
        assert record is not None and record.status is JobRunStatus.CLAIMED
        assert connection.execute(
            text("SELECT count(*) FROM audit_events "
                 "WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
            {"id": str(run.job_run_id)},
        ).scalar_one() == 0
        assert jobs.complete(create_job_run_completion(
            run, JobRunStatus.FAILED, scheduled_for + timedelta(minutes=2),
            failure_code="EXPECTED_AUDIT_SAVEPOINT_ROLLBACK",
        ))
    engine.dispose()
