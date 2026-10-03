import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_reconciliation_receipt_is_unique_per_job_run() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    entity_id = str(uuid4())
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "INSERT INTO audit_events(audit_event_id, event_type, entity_type, entity_id, payload) "
                    "VALUES (:id, 'PAPER_RUN_RECONCILED', 'JOB_RUN', :entity_id, CAST(:payload AS JSONB))"
                ),
                {"id": uuid4(), "entity_id": entity_id, "payload": '{"schema_version":1}'},
            )

            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO audit_events(audit_event_id, event_type, entity_type, entity_id, payload) "
                            "VALUES (:id, 'PAPER_RUN_RECONCILED', 'JOB_RUN', :entity_id, CAST(:payload AS JSONB))"
                        ),
                        {"id": uuid4(), "entity_id": entity_id, "payload": '{"schema_version":1}'},
                    )

            count = connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE event_type = 'PAPER_RUN_RECONCILED' "
                    "AND entity_type = 'JOB_RUN' AND entity_id = :entity_id"
                ),
                {"entity_id": entity_id},
            ).scalar_one()
            assert count == 1
        finally:
            transaction.rollback()
