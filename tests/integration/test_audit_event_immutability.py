import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_audit_events_are_append_only() -> None:
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
            audit_event_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO audit_events(audit_event_id, event_type, entity_type, entity_id, payload) "
                    "VALUES (:id, 'TEST_EVENT', 'TEST_ENTITY', 'entity-1', CAST(:payload AS JSONB))"
                ),
                {"id": audit_event_id, "payload": '{"state":"original"}'},
            )

            with pytest.raises(IntegrityError, match="AUDIT_EVENT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE audit_events SET payload = CAST(:payload AS JSONB) WHERE audit_event_id = :id"),
                        {"id": audit_event_id, "payload": '{"state":"rewritten"}'},
                    )

            with pytest.raises(IntegrityError, match="AUDIT_EVENT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("DELETE FROM audit_events WHERE audit_event_id = :id"),
                        {"id": audit_event_id},
                    )

            stored = connection.execute(
                text("SELECT event_type, entity_type, entity_id, payload FROM audit_events WHERE audit_event_id = :id"),
                {"id": audit_event_id},
            ).one()
            assert stored.event_type == "TEST_EVENT"
            assert stored.entity_type == "TEST_ENTITY"
            assert stored.entity_id == "entity-1"
            assert stored.payload == {"state": "original"}
        finally:
            transaction.rollback()
