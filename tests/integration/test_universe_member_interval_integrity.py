import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_universe_member_validity_interval_is_enforced_by_postgres() -> None:
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
            instrument_id = uuid4()
            start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:id, 'TEST', 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:id, 'interval-test')"),
                {"id": universe_id},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_versions("
                    "universe_version_id, universe_id, version, pit_certified, declared_member_count"
                    ") VALUES (:vid, :uid, 'v1', TRUE, 1)"
                ),
                {"vid": universe_version_id, "uid": universe_id},
            )

            for invalid_end in (start, start - timedelta(seconds=1)):
                with pytest.raises(IntegrityError, match="ck_universe_members_valid_interval"):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "INSERT INTO universe_members("
                                "universe_version_id, instrument_id, valid_from, valid_to"
                                ") VALUES (:vid, :iid, :valid_from, :valid_to)"
                            ),
                            {
                                "vid": universe_version_id,
                                "iid": instrument_id,
                                "valid_from": start,
                                "valid_to": invalid_end,
                            },
                        )

            connection.execute(
                text(
                    "INSERT INTO universe_members("
                    "universe_version_id, instrument_id, valid_from, valid_to"
                    ") VALUES (:vid, :iid, :valid_from, :valid_to)"
                ),
                {
                    "vid": universe_version_id,
                    "iid": instrument_id,
                    "valid_from": start,
                    "valid_to": start + timedelta(days=1),
                },
            )
        finally:
            transaction.rollback()
