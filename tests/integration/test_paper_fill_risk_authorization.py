import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import (
    PaperCycleContext,
    PaperOrderWriter,
    PaperRiskWriter,
    PaperSignalWriter,
)
from hope.application.paper.fills import PaperFillWriter
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc


@pytest.mark.integration
def test_canonical_paper_fill_requires_durable_approved_risk() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-fill-risk-authorization",
        datetime(2026, 10, 13, 10, 30, tzinfo=UTC),
    )
    context = PaperCycleContext(run)
    decision_time = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-fill-risk-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash="a" * 64,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-fill-risk-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash="a" * 64,
    )
    order = Order(
        order_id=context.order_id(signal_id),
        signal_id=signal_id,
        instrument_id=instrument_id,
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )
    fill = Fill(
        context.fill_id(signal_id, 0),
        order.order_id,
        signal_id,
        instrument_id,
        OrderSide.BUY,
        Decimal("1"),
        Decimal("100"),
        Decimal("0.10"),
        Decimal("0.05"),
        "cost-v1",
        datetime(2026, 10, 13, 10, 1, tzinfo=UTC),
    )

    try:
        with engine.begin() as connection:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments("
                    "instrument_id, canonical_symbol, exchange, status"
                    ") VALUES (:id, 'PAPER-FILL-RISK', 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id},
            )
            assert SqlAlchemyJobRunRepository(connection).claim(run)
            assert PaperSignalWriter(
                SqlAlchemyPaperSignalRepository(connection)
            ).record(context, signal)
            assert PaperOrderWriter(
                SqlAlchemyPaperOrderRepository(connection)
            ).record(context, order)

            writer = PaperFillWriter(SqlAlchemyPaperFillRepository(connection))
            with pytest.raises(
                IntegrityError,
                match="PAPER_FILL_RISK_AUTHORIZATION_MISSING",
            ):
                writer.record(context, fill, sequence=0)

            assert connection.execute(
                text("SELECT count(*) FROM fills WHERE fill_id=:fill_id"),
                {"fill_id": fill.fill_id},
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_effects "
                    "WHERE effect_type='FILL' AND entity_id=:fill_id"
                ),
                {"fill_id": fill.fill_id},
            ).scalar_one() == 0

            assert PaperRiskWriter(
                SqlAlchemyPaperRiskRepository(connection)
            ).record(
                context,
                RiskAssessment(
                    signal_id=signal_id,
                    decision=RiskDecision.APPROVE,
                    reason_code="TEST_APPROVED",
                    approved_quantity=order.quantity,
                ),
            )
            assert writer.record(context, fill, sequence=0)
    finally:
        engine.dispose()
