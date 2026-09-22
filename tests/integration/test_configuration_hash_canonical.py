import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
@pytest.mark.parametrize("configuration_hash", ["", "a" * 63, "A" * 64, "a" * 64 + " "])
def test_configuration_snapshot_hash_is_canonical_sha256(configuration_hash: str) -> None:
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
            with pytest.raises(IntegrityError, match="ck_configuration_snapshots_hash_canonical"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO configuration_snapshots(configuration_hash, canonical_json) "
                            "VALUES (:configuration_hash, CAST('{}' AS JSONB))"
                        ),
                        {"configuration_hash": configuration_hash},
                    )
        finally:
            transaction.rollback()
            engine.dispose()
