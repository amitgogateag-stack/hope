import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


@pytest.mark.integration
def test_paper_risk_assessment_cannot_be_modified_or_deleted() -> None:
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
            instrument_id = uuid4()
            signal_id = uuid4()
            decision_time = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
            job_run = create_scheduled_job_run("paper-risk-immutability", decision_time)
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, 'PAPER-RISK-IMM', 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:signal_id, :instrument_id, :decision_time, 'SIGNAL')"
                ),
                {"signal_id": signal_id, "instrument_id": instrument_id, "decision_time": decision_time},
            )

            jobs = SqlAlchemyJobRunRepository(connection)
            assert jobs.claim(job_run) is True
            for effect_type, payload_hash in (("SIGNAL", "a" * 64), ("RISK", "b" * 64)):
                connection.execute(
                    text(
                        "INSERT INTO paper_effects(effect_id, job_run_id, effect_type, entity_id, payload_hash) "
                        "VALUES (:effect_id, :job_run_id, :effect_type, :signal_id, :payload_hash)"
                    ),
                    {
                        "effect_id": uuid4(),
                        "job_run_id": job_run.job_run_id,
                        "effect_type": effect_type,
                        "signal_id": signal_id,
                        "payload_hash": payload_hash,
                    },
                )

            connection.execute(
                text(
                    "INSERT INTO paper_risk_assessments"
                    "(signal_id, decision, reason_code, approved_quantity) "
                    "VALUES (:signal_id, 'APPROVE', 'PORTFOLIO_RISK_APPROVED', :quantity)"
                ),
                {"signal_id": signal_id, "quantity": Decimal("1")},
            )

            with pytest.raises(IntegrityError, match="PAPER_RISK_ASSESSMENT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE paper_risk_assessments SET reason_code='CHANGED' WHERE signal_id=:signal_id"),
                        {"signal_id": signal_id},
                    )

            with pytest.raises(IntegrityError, match="PAPER_RISK_ASSESSMENT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("DELETE FROM paper_risk_assessments WHERE signal_id=:signal_id"),
                        {"signal_id": signal_id},
                    )

            stored = connection.execute(
                text(
                    "SELECT decision, reason_code, approved_quantity "
                    "FROM paper_risk_assessments WHERE signal_id=:signal_id"
                ),
                {"signal_id": signal_id},
            ).mappings().one()
            assert stored["decision"] == "APPROVE"
            assert stored["reason_code"] == "PORTFOLIO_RISK_APPROVED"
            assert stored["approved_quantity"] == Decimal("1")
        finally:
            transaction.rollback()
