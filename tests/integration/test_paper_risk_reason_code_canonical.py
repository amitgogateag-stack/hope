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
def test_paper_risk_reason_code_must_be_canonical() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    signal_id = uuid4()
    decision_time = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
    job_run = create_scheduled_job_run("paper-risk-reason-canonical", decision_time)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:instrument_id, 'PAPER-RISK-REASON', 'TEST', 'ACTIVE')"
            ),
            {"instrument_id": instrument_id},
        )
        connection.execute(
            text(
                "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                "VALUES (:signal_id, :instrument_id, :decision_time, 'SIGNAL')"
            ),
            {
                "signal_id": signal_id,
                "instrument_id": instrument_id,
                "decision_time": decision_time,
            },
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

        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO paper_risk_assessments"
                        "(signal_id, decision, reason_code, approved_quantity) "
                        "VALUES (:signal_id, 'APPROVE', ' APPROVED ', :quantity)"
                    ),
                    {"signal_id": signal_id, "quantity": Decimal("1")},
                )

        connection.execute(
            text(
                "INSERT INTO paper_risk_assessments"
                "(signal_id, decision, reason_code, approved_quantity) "
                "VALUES (:signal_id, 'APPROVE', 'APPROVED', :quantity)"
            ),
            {"signal_id": signal_id, "quantity": Decimal("1")},
        )
