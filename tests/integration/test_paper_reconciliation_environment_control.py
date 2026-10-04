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
