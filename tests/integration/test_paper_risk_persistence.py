import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.risk import PaperRiskWriter
from hope.application.paper.signals import PaperSignalWriter
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


UTC = timezone.utc
INPUTS_HASH = "e" * 64


def _signal(context: PaperCycleContext, instrument_id, strategy_version: str) -> Signal:
    decision_time = datetime(2026, 9, 13, 14, 59, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version=strategy_version,
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7"),
        inputs_hash=INPUTS_HASH,
    )
    return Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version=strategy_version,
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.7"),
        inputs_hash=INPUTS_HASH,
    )


def _assessment(signal: Signal) -> RiskAssessment:
    return RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="APPROVED",
        approved_quantity=Decimal("2"),
    )


@pytest.mark.integration
def test_paper_risk_requires_tracked_signal_and_is_idempotent() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    job_run = create_scheduled_job_run(
        "paper-risk-persist",
        datetime(2026, 9, 13, 15, 0, tzinfo=UTC),
    )
    context = PaperCycleContext(job_run)
    untracked_signal = _signal(context, instrument_id, "paper-risk-untracked-v1")
    tracked_signal = _signal(context, instrument_id, "paper-risk-tracked-v1")

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:instrument_id, 'PAPER-RISK', 'TEST', 'ACTIVE')"
            ),
            {"instrument_id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(job_run) is True

        risk_writer = PaperRiskWriter(SqlAlchemyPaperRiskRepository(connection))
        connection.execute(
            text(
                "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                "VALUES (:signal_id, :instrument_id, :decision_time, 'SIGNAL')"
            ),
            {
                "signal_id": untracked_signal.signal_id,
                "instrument_id": untracked_signal.instrument_id,
                "decision_time": untracked_signal.decision_time,
            },
        )
        with pytest.raises(ValueError, match="PAPER_RISK_SOURCE_SIGNAL_UNTRACKED"):
            risk_writer.record(context, _assessment(untracked_signal))
        assert connection.execute(
            text("SELECT count(*) FROM paper_risk_assessments WHERE signal_id=:signal_id"),
            {"signal_id": untracked_signal.signal_id},
        ).scalar_one() == 0

        signal_writer = PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection))
        assert signal_writer.record(context, tracked_signal) is True
        assessment = _assessment(tracked_signal)
        assert risk_writer.record(context, assessment) is True
        assert risk_writer.record(context, assessment) is False

        row = connection.execute(
            text(
                "SELECT decision, reason_code, approved_quantity "
                "FROM paper_risk_assessments WHERE signal_id=:signal_id"
            ),
            {"signal_id": tracked_signal.signal_id},
        ).mappings().one()
        assert row["decision"] == "APPROVE"
        assert row["reason_code"] == "APPROVED"
        assert row["approved_quantity"] == Decimal("2")
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE effect_type='RISK' AND entity_id=:signal_id"
            ),
            {"signal_id": tracked_signal.signal_id},
        ).scalar_one() == 1
