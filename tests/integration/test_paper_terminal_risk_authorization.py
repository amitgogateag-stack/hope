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
from hope.application.paper.terminals import PaperTerminalWriter
from hope.domain.execution.models import (
    Environment,
    ExecutionRejection,
    Order,
    OrderSide,
)
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
from hope.infrastructure.repositories.paper_terminals import SqlAlchemyPaperTerminalRepository

UTC = timezone.utc


@pytest.mark.integration
def test_canonical_paper_terminal_requires_durable_approved_risk() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-terminal-risk-authorization",
        datetime(2026, 10, 13, 11, 30, tzinfo=UTC),
    )
    context = PaperCycleContext(run)
    decision_time = datetime(2026, 10, 13, 11, 0, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-terminal-risk-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash="b" * 64,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-terminal-risk-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash="b" * 64,
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
    outcome = ExecutionRejection(
        order.order_id,
        signal_id,
        instrument_id,
        Environment.PAPER,
        "TEST_REJECT",
        datetime(2026, 10, 13, 11, 1, tzinfo=UTC),
    )

    try:
        with engine.begin() as connection:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments("
                    "instrument_id, canonical_symbol, exchange, status"
                    ") VALUES (:id, 'PAPER-TERMINAL-RISK', 'TEST', 'ACTIVE')"
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

            writer = PaperTerminalWriter(
                SqlAlchemyPaperTerminalRepository(connection)
            )
            with pytest.raises(
                IntegrityError,
                match="PAPER_TERMINAL_RISK_AUTHORIZATION_MISSING",
            ):
                writer.record(context, outcome)

            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_order_terminal_events "
                    "WHERE order_id=:order_id"
                ),
                {"order_id": order.order_id},
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_effects "
                    "WHERE effect_type='REJECTION' AND entity_id=:order_id"
                ),
                {"order_id": order.order_id},
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
            assert writer.record(context, outcome)
    finally:
        engine.dispose()
