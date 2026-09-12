import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_staging_dataset_version_identity_cannot_change_before_seal() -> None:
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
            other_dataset_id = uuid4()
            dataset_version_id = uuid4()

            for current_dataset_id in (dataset_id, other_dataset_id):
                connection.execute(
                    text(
                        "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                        "VALUES (:dataset_id, :name, 'TEST', TRUE)"
                    ),
                    {
                        "dataset_id": current_dataset_id,
                        "name": f"identity-{current_dataset_id}",
                    },
                )

            connection.execute(
                text(
                    "INSERT INTO dataset_versions("
                    "dataset_version_id, dataset_id, version, vintage_label, immutable"
                    ") VALUES (:version_id, :dataset_id, 'v1', 'staging', FALSE)"
                ),
                {"version_id": dataset_version_id, "dataset_id": dataset_id},
            )

            identity_updates = (
                (
                    "UPDATE dataset_versions SET dataset_version_id = :replacement "
                    "WHERE dataset_version_id = :version_id",
                    {"replacement": uuid4(), "version_id": dataset_version_id},
                ),
                (
                    "UPDATE dataset_versions SET dataset_id = :replacement "
                    "WHERE dataset_version_id = :version_id",
                    {"replacement": other_dataset_id, "version_id": dataset_version_id},
                ),
                (
                    "UPDATE dataset_versions SET version = 'v2' "
                    "WHERE dataset_version_id = :version_id",
                    {"version_id": dataset_version_id},
                ),
            )

            for statement, params in identity_updates:
                with pytest.raises(IntegrityError, match="DATASET_VERSION_IDENTITY_IMMUTABLE"):
                    with connection.begin_nested():
                        connection.execute(text(statement), params)

            connection.execute(
                text(
                    "UPDATE dataset_versions SET immutable = TRUE, vintage_label = 'sealed' "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            )

            row = connection.execute(
                text(
                    "SELECT dataset_id, version, vintage_label, immutable "
                    "FROM dataset_versions WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).one()
            assert row.dataset_id == dataset_id
            assert row.version == "v1"
            assert row.vintage_label == "sealed"
            assert row.immutable is True
        finally:
            transaction.rollback()
            engine.dispose()
