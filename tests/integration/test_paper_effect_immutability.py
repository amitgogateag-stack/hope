import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_effect_history_cannot_be_modified_or_deleted() -> None:
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
            job_run_id = uuid4()
            effect_id = uuid4()
            entity_id = uuid4()
            scheduled_for = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)

            connection.execute(
                text(
                    "INSERT INTO job_runs(job_run_id, job_key, scheduled_for) "
                    "VALUES (:job_run_id, :job_key, :scheduled_for)"
                ),
                {
                    "job_run_id": job_run_id,
                    "job_key": f"paper-effect-immutability-{job_run_id}",
                    "scheduled_for": scheduled_for,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO paper_effects(effect_id, job_run_id, effect_type, entity_id, payload_hash) "
                    "VALUES (:effect_id, :job_run_id, 'SIGNAL', :entity_id, :payload_hash)"
                ),
                {
                    "effect_id": effect_id,
                    "job_run_id": job_run_id,
                    "entity_id": entity_id,
                    "payload_hash": "a" * 64,
                },
            )

            with pytest.raises(IntegrityError, match="PAPER_EFFECT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE paper_effects SET payload_hash = :payload_hash WHERE effect_id = :effect_id"),
                        {"effect_id": effect_id, "payload_hash": "b" * 64},
                    )

            with pytest.raises(IntegrityError, match="PAPER_EFFECT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("DELETE FROM paper_effects WHERE effect_id = :effect_id"),
                        {"effect_id": effect_id},
                    )

            stored = connection.execute(
                text(
                    "SELECT job_run_id, effect_type, entity_id, payload_hash "
                    "FROM paper_effects WHERE effect_id = :effect_id"
                ),
                {"effect_id": effect_id},
            ).mappings().one()
            assert stored["job_run_id"] == job_run_id
            assert stored["effect_type"] == "SIGNAL"
            assert stored["entity_id"] == entity_id
            assert stored["payload_hash"] == "a" * 64
        finally:
            transaction.rollback()
