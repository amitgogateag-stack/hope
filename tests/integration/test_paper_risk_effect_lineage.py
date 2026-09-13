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
def test_paper_risk_assessment_requires_signal_and_risk_effect_lineage() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    signal_without_source_effect = uuid4()
    signal_without_risk_effect = uuid4()
    decision_time = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
    job_run = create_scheduled_job_run("paper-risk-lineage", decision_time)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:instrument_id, 'PAPER-RISK-LINEAGE', 'TEST', 'ACTIVE')"
            ),
            {"instrument_id": instrument_id},
        )
        for signal_id in (signal_without_source_effect, signal_without_risk_effect):
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

        connection.execute(
            text(
                "INSERT INTO paper_effects(effect_id, job_run_id, effect_type, entity_id, payload_hash) "
                "VALUES (:effect_id, :job_run_id, 'RISK', :signal_id, :payload_hash)"
            ),
            {
                "effect_id": uuid4(),
                "job_run_id": job_run.job_run_id,
                "signal_id": signal_without_source_effect,
                "payload_hash": "a" * 64,
            },
        )
        with pytest.raises(IntegrityError, match="PAPER_RISK_SOURCE_SIGNAL_UNTRACKED"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO paper_risk_assessments"
                        "(signal_id, decision, reason_code, approved_quantity) "
                        "VALUES (:signal_id, 'APPROVE', 'APPROVED', :quantity)"
                    ),
                    {"signal_id": signal_without_source_effect, "quantity": Decimal("1")},
                )

        connection.execute(
            text(
                "INSERT INTO paper_effects(effect_id, job_run_id, effect_type, entity_id, payload_hash) "
                "VALUES (:effect_id, :job_run_id, 'SIGNAL', :signal_id, :payload_hash)"
            ),
            {
                "effect_id": uuid4(),
                "job_run_id": job_run.job_run_id,
                "signal_id": signal_without_risk_effect,
                "payload_hash": "b" * 64,
            },
        )
        with pytest.raises(IntegrityError, match="PAPER_RISK_EFFECT_UNTRACKED"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO paper_risk_assessments"
                        "(signal_id, decision, reason_code, approved_quantity) "
                        "VALUES (:signal_id, 'APPROVE', 'APPROVED', :quantity)"
                    ),
                    {"signal_id": signal_without_risk_effect, "quantity": Decimal("1")},
                )
