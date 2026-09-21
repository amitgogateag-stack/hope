import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
@pytest.mark.parametrize("version", ["", " ", " v1", "v1 "])
def test_universe_version_rejects_noncanonical_version(version: str) -> None:
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
            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:id, 'contract-test')"),
                {"id": universe_id},
            )
            with pytest.raises(IntegrityError, match="ck_universe_versions_version_canonical"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO universe_versions("
                            "universe_version_id, universe_id, version, pit_certified, declared_member_count"
                            ") VALUES (:vid, :uid, :version, FALSE, 0)"
                        ),
                        {"vid": uuid4(), "uid": universe_id, "version": version},
                    )
        finally:
            transaction.rollback()
