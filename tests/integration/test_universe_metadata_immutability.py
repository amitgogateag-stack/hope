import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_universe_metadata_freezes_once_a_version_exists() -> None:
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
            universe_id = uuid4()
            universe_version_id = uuid4()

            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:id, 'draft-name')"),
                {"id": universe_id},
            )
            connection.execute(
                text("UPDATE universes SET name = 'corrected-name' WHERE universe_id = :id"),
                {"id": universe_id},
            )

            connection.execute(
                text(
                    "INSERT INTO universe_versions("
                    "universe_version_id, universe_id, version, pit_certified, declared_member_count"
                    ") VALUES (:vid, :uid, 'v1', TRUE, 0)"
                ),
                {"vid": universe_version_id, "uid": universe_id},
            )

            with pytest.raises(IntegrityError, match="UNIVERSE_METADATA_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE universes SET name = 'rewritten-name' WHERE universe_id = :id"),
                        {"id": universe_id},
                    )

            with pytest.raises(IntegrityError, match="UNIVERSE_METADATA_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("DELETE FROM universes WHERE universe_id = :id"),
                        {"id": universe_id},
                    )

            stored_name = connection.execute(
                text("SELECT name FROM universes WHERE universe_id = :id"),
                {"id": universe_id},
            ).scalar_one()
            assert stored_name == "corrected-name"
        finally:
            transaction.rollback()
