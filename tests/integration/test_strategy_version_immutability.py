import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_strategy_versions_are_append_only() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    strategy_id = uuid4()
    strategy_version_id = uuid4()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "INSERT INTO strategies(strategy_id, name, family) "
                    "VALUES (:strategy_id, :name, :family)"
                ),
                {
                    "strategy_id": strategy_id,
                    "name": f"immutability-{strategy_id}",
                    "family": "TEST",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                    "VALUES (:strategy_version_id, :strategy_id, :version, :code_commit)"
                ),
                {
                    "strategy_version_id": strategy_version_id,
                    "strategy_id": strategy_id,
                    "version": "1.0.0",
                    "code_commit": "commit-a",
                },
            )

            with pytest.raises(IntegrityError, match="STRATEGY_VERSION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE strategy_versions SET code_commit = :code_commit "
                            "WHERE strategy_version_id = :strategy_version_id"
                        ),
                        {
                            "strategy_version_id": strategy_version_id,
                            "code_commit": "commit-b",
                        },
                    )

            with pytest.raises(IntegrityError, match="STRATEGY_VERSION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM strategy_versions "
                            "WHERE strategy_version_id = :strategy_version_id"
                        ),
                        {"strategy_version_id": strategy_version_id},
                    )

            stored = connection.execute(
                text(
                    "SELECT version, code_commit FROM strategy_versions "
                    "WHERE strategy_version_id = :strategy_version_id"
                ),
                {"strategy_version_id": strategy_version_id},
            ).one()
            assert stored == ("1.0.0", "commit-a")
        finally:
            transaction.rollback()
