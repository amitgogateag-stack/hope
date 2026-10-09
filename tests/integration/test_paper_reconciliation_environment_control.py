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
    engine.dispose()
