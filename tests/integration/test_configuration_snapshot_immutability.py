import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_configuration_snapshots_are_append_only() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    configuration_hash = "a" * 64
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "INSERT INTO configuration_snapshots(configuration_hash, canonical_json) "
                    "VALUES (:configuration_hash, CAST(:canonical_json AS JSONB))"
                ),
                {"configuration_hash": configuration_hash, "canonical_json": '{"risk":1}'},
            )

            with pytest.raises(IntegrityError, match="CONFIGURATION_SNAPSHOT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE configuration_snapshots "
                            "SET canonical_json = CAST(:canonical_json AS JSONB) "
                            "WHERE configuration_hash = :configuration_hash"
                        ),
                        {"configuration_hash": configuration_hash, "canonical_json": '{"risk":2}'},
                    )

            with pytest.raises(IntegrityError, match="CONFIGURATION_SNAPSHOT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM configuration_snapshots "
                            "WHERE configuration_hash = :configuration_hash"
                        ),
                        {"configuration_hash": configuration_hash},
                    )

            stored = connection.execute(
                text(
                    "SELECT canonical_json FROM configuration_snapshots "
                    "WHERE configuration_hash = :configuration_hash"
                ),
                {"configuration_hash": configuration_hash},
            ).scalar_one()
            assert stored == {"risk": 1}
        finally:
            transaction.rollback()
