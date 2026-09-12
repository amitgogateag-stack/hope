import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_dataset_metadata_cannot_change_after_a_version_is_sealed() -> None:
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
            dataset_id = uuid4()
            dataset_version_id = uuid4()

            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                    "VALUES (:dataset_id, 'staging', 'SOURCE_A', FALSE)"
                ),
                {"dataset_id": dataset_id},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions("
                    "dataset_version_id, dataset_id, version, vintage_label, immutable"
                    ") VALUES (:version_id, :dataset_id, 'v1', 'staging', FALSE)"
                ),
                {"version_id": dataset_version_id, "dataset_id": dataset_id},
            )

            connection.execute(
                text(
                    "UPDATE datasets SET name = 'prepared', source = 'SOURCE_B', pit_certified = TRUE "
                    "WHERE dataset_id = :dataset_id"
                ),
                {"dataset_id": dataset_id},
            )
            connection.execute(
                text(
                    "UPDATE dataset_versions SET immutable = TRUE, vintage_label = 'sealed' "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            )

            for statement in (
                "UPDATE datasets SET name = 'rewritten' WHERE dataset_id = :dataset_id",
                "UPDATE datasets SET source = 'SOURCE_C' WHERE dataset_id = :dataset_id",
                "UPDATE datasets SET pit_certified = FALSE WHERE dataset_id = :dataset_id",
                "DELETE FROM datasets WHERE dataset_id = :dataset_id",
            ):
                with pytest.raises(IntegrityError, match="DATASET_METADATA_IMMUTABLE"):
                    with connection.begin_nested():
                        connection.execute(text(statement), {"dataset_id": dataset_id})

            row = connection.execute(
                text(
                    "SELECT name, source, pit_certified FROM datasets "
                    "WHERE dataset_id = :dataset_id"
                ),
                {"dataset_id": dataset_id},
            ).one()
            assert row.name == "prepared"
            assert row.source == "SOURCE_B"
            assert row.pit_certified is True
        finally:
            transaction.rollback()
            engine.dispose()
