import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperRiskWriter
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.risk import paper_risk_payload_hash
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository


def _setup_lineage(connection, migrations_dir):
    apply_migrations(connection, migrations_dir)
    instrument_id, signal_id = uuid4(), uuid4()
    decision_time = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    run = create_scheduled_job_run(
        f"paper-risk-payload:{uuid4()}",
        decision_time,
    )
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"RISK-PAYLOAD-{instrument_id.hex[:12]}"},
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
    assert SqlAlchemyJobRunRepository(connection).claim(run) is True
    signal_effect = create_paper_effect(
        run,
        PaperEffectType.SIGNAL,
        signal_id,
        "a" * 64,
    )
    connection.execute(
        text(
            "INSERT INTO paper_effects(effect_id, job_run_id, effect_type, entity_id, payload_hash) "
            "VALUES (:effect_id, :job_run_id, 'SIGNAL', :signal_id, :payload_hash)"
        ),
        {
            "effect_id": signal_effect.effect_id,
            "job_run_id": run.job_run_id,
            "signal_id": signal_id,
            "payload_hash": "a" * 64,
        },
    )
    return run, signal_id


@pytest.mark.integration
def test_paper_risk_effect_payload_is_database_authenticated() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        run, signal_id = _setup_lineage(connection, migrations_dir)
        assessment = RiskAssessment(
            signal_id=signal_id,
            decision=RiskDecision.APPROVE,
            reason_code="TEST_APPROVED",
            approved_quantity=Decimal("1.2500"),
        )
        assert PaperRiskWriter(
            SqlAlchemyPaperRiskRepository(connection)
        ).record(PaperCycleContext(run), assessment) is True

        row = connection.execute(
            text(
                "SELECT effect.payload_hash, "
                "hope_paper_risk_payload_hash(assessment.signal_id, assessment.decision, "
                "assessment.reason_code, assessment.approved_quantity) AS database_hash "
                "FROM paper_risk_assessments assessment "
                "JOIN paper_effects effect ON effect.effect_type='RISK' "
                "AND effect.entity_id=assessment.signal_id "
                "WHERE assessment.signal_id=:signal_id"
            ),
            {"signal_id": signal_id},
        ).one()
        assert row.payload_hash == paper_risk_payload_hash(assessment)
        assert row.database_hash == row.payload_hash

    engine.dispose()


@pytest.mark.integration
def test_paper_risk_payload_conflict_rolls_back_both_sides() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        run, signal_id = _setup_lineage(connection, migrations_dir)
        with pytest.raises(
            IntegrityError,
            match="PAPER_RISK_EFFECT_PAYLOAD_MISMATCH",
        ):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO paper_effects(effect_id, job_run_id, effect_type, entity_id, payload_hash) "
                        "VALUES (:effect_id, :job_run_id, 'RISK', :signal_id, :payload_hash)"
                    ),
                    {
                        "effect_id": uuid4(),
                        "job_run_id": run.job_run_id,
                        "signal_id": signal_id,
                        "payload_hash": "f" * 64,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO paper_risk_assessments(signal_id, decision, reason_code, approved_quantity) "
                        "VALUES (:signal_id, 'APPROVE', 'TEST_APPROVED', 1)"
                    ),
                    {"signal_id": signal_id},
                )

        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE effect_type='RISK' AND entity_id=:signal_id"
            ),
            {"signal_id": signal_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_risk_assessments "
                "WHERE signal_id=:signal_id"
            ),
            {"signal_id": signal_id},
        ).scalar_one() == 0

    engine.dispose()


@pytest.mark.integration
def test_orphan_paper_risk_effect_cannot_commit() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with pytest.raises(
        IntegrityError,
        match="PAPER_RISK_EFFECT_WITHOUT_ASSESSMENT",
    ):
        with engine.begin() as connection:
            run, signal_id = _setup_lineage(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO paper_effects(effect_id, job_run_id, effect_type, entity_id, payload_hash) "
                    "VALUES (:effect_id, :job_run_id, 'RISK', :signal_id, :payload_hash)"
                ),
                {
                    "effect_id": uuid4(),
                    "job_run_id": run.job_run_id,
                    "signal_id": signal_id,
                    "payload_hash": "b" * 64,
                },
            )

    engine.dispose()
