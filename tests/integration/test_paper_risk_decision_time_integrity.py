import os
from datetime import datetime, timedelta, timezone
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
def test_paper_risk_decision_cannot_follow_job_schedule() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    scheduled_for = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
    job_run = create_scheduled_job_run("paper-risk-decision-time", scheduled_for)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(job_run) is True

        def insert_lineage(decision_time):
            instrument_id = uuid4()
            signal_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id, "symbol": f"PAPER-RISK-{signal_id}"},
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
            for effect_type, payload_hash in (("SIGNAL", "a" * 64), ("RISK", "b" * 64)):
                connection.execute(
                    text(
                        "INSERT INTO paper_effects"
                        "(effect_id, job_run_id, effect_type, entity_id, payload_hash) "
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
            return signal_id

        future_signal_id = insert_lineage(scheduled_for + timedelta(microseconds=1))
        with pytest.raises(IntegrityError, match="PAPER_RISK_DECISION_AFTER_JOB_SCHEDULE"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO paper_risk_assessments"
                        "(signal_id, decision, reason_code, approved_quantity) "
                        "VALUES (:signal_id, 'APPROVE', 'PORTFOLIO_RISK_APPROVED', :quantity)"
                    ),
                    {"signal_id": future_signal_id, "quantity": Decimal("1")},
                )

        valid_signal_id = insert_lineage(scheduled_for)
        connection.execute(
            text(
                "INSERT INTO paper_risk_assessments"
                "(signal_id, decision, reason_code, approved_quantity) "
                "VALUES (:signal_id, 'APPROVE', 'PORTFOLIO_RISK_APPROVED', :quantity)"
            ),
            {"signal_id": valid_signal_id, "quantity": Decimal("1")},
        )
