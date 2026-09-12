import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_strategy_metadata_freezes_once_a_version_exists() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            strategy_id = uuid4()
            strategy_version_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO strategies(strategy_id, name, family) "
                    "VALUES (:strategy_id, :name, 'draft-family')"
                ),
                {"strategy_id": strategy_id, "name": f"draft-{strategy_id}"},
            )

            # Draft metadata remains correctable until a durable version exists.
            connection.execute(
                text("UPDATE strategies SET family = 'final-family' WHERE strategy_id = :strategy_id"),
                {"strategy_id": strategy_id},
            )
            connection.execute(
                text(
                    "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                    "VALUES (:version_id, :strategy_id, 'v1', 'abc123')"
                ),
                {"version_id": strategy_version_id, "strategy_id": strategy_id},
            )

            with pytest.raises(IntegrityError, match="STRATEGY_METADATA_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE strategies SET name = 'rewritten' WHERE strategy_id = :strategy_id"),
                        {"strategy_id": strategy_id},
                    )

            with pytest.raises(IntegrityError, match="STRATEGY_METADATA_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE strategies SET family = 'rewritten-family' WHERE strategy_id = :strategy_id"),
                        {"strategy_id": strategy_id},
                    )

            stored = connection.execute(
                text("SELECT family FROM strategies WHERE strategy_id = :strategy_id"),
                {"strategy_id": strategy_id},
            ).scalar_one()
            assert stored == "final-family"
        finally:
            transaction.rollback()
