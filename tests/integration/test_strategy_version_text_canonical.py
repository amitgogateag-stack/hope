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
        ("version", "", "ck_strategy_versions_version_canonical"),
        ("version", " v1", "ck_strategy_versions_version_canonical"),
        ("version", "v1 ", "ck_strategy_versions_version_canonical"),
        ("code_commit", "", "ck_strategy_versions_code_commit_canonical"),
        ("code_commit", " abc123", "ck_strategy_versions_code_commit_canonical"),
        ("code_commit", "abc123 ", "ck_strategy_versions_code_commit_canonical"),
    ],
)
def test_strategy_version_provenance_text_is_canonical(column: str, value: str, constraint: str) -> None:
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
            connection.execute(
                text("INSERT INTO strategies(strategy_id, name, family) VALUES (:id, :name, 'TEST')"),
                {"id": strategy_id, "name": f"canonical-{strategy_id}"},
            )
            values = {"version": "v1", "code_commit": "abc123"}
            values[column] = value
            with pytest.raises(IntegrityError, match=constraint):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                            "VALUES (:vid, :sid, :version, :code_commit)"
                        ),
                        {"vid": uuid4(), "sid": strategy_id, **values},
                    )
        finally:
            transaction.rollback()
            engine.dispose()
