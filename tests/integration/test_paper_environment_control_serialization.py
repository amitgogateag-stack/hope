import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.paper_control import (
    SqlAlchemyPaperEnvironmentControlRepository,
)


@pytest.mark.integration
def test_running_guard_serializes_concurrent_halt_transition() -> None:
    """A HALT cannot cross a transaction that has admitted protected PAPER work."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as guard_connection:
        guard_transaction = guard_connection.begin()
        try:
            guard_repository = SqlAlchemyPaperEnvironmentControlRepository(
                guard_connection
            )
            guard_repository.assert_running()
            event_count = guard_connection.execute(
                text("SELECT count(*) FROM paper_environment_control_events")
            ).scalar_one()

            with engine.connect() as contender_connection:
                contender_connection.execute(text("SET lock_timeout TO '100ms'"))
                contender_repository = SqlAlchemyPaperEnvironmentControlRepository(
                    contender_connection
                )
                with pytest.raises(OperationalError):
                    contender_repository.transition(
                        "RUNNING",
                        "HALTED",
                        "CONCURRENT_HALT_TEST",
                        actor="integration-test",
                    )
                contender_connection.rollback()

            assert guard_connection.execute(
                text("SELECT count(*) FROM paper_environment_control_events")
            ).scalar_one() == event_count
            assert guard_repository.current_state() == "RUNNING"
        finally:
            guard_transaction.rollback()

    engine.dispose()
