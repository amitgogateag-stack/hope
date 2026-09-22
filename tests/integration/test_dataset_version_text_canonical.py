import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("version", "", "ck_dataset_versions_version_canonical"),
        ("version", " v1", "ck_dataset_versions_version_canonical"),
        ("version", "v1 ", "ck_dataset_versions_version_canonical"),
        ("vintage_label", "", "ck_dataset_versions_vintage_label_canonical"),
        ("vintage_label", " staging", "ck_dataset_versions_vintage_label_canonical"),
        ("vintage_label", "staging ", "ck_dataset_versions_vintage_label_canonical"),
    ],
)
def test_dataset_version_provenance_text_is_canonical(column: str, value: str, constraint: str) -> None:
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
            connection.execute(
                text("INSERT INTO datasets(dataset_id, name, source, pit_certified) VALUES (:id, :name, 'TEST', TRUE)"),
                {"id": dataset_id, "name": f"canonical-{dataset_id}"},
            )
            values = {"version": "v1", "vintage_label": "staging"}
            values[column] = value
            with pytest.raises(IntegrityError, match=constraint):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label, immutable) "
                            "VALUES (:vid, :did, :version, :vintage_label, FALSE)"
                        ),
                        {"vid": uuid4(), "did": dataset_id, **values},
                    )
        finally:
            transaction.rollback()
            engine.dispose()
