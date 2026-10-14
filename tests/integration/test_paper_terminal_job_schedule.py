import os
from datetime import datetime, timedelta, timezone
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
from hope.domain.execution import (
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
from hope.infrastructure.repositories.paper_terminals import (
    SqlAlchemyPaperTerminalRepository,
)


UTC = timezone.utc


@pytest.mark.integration
def test_postgres_rejects_future_terminal_through_direct_repository_boundary() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    job_run = create_scheduled_job_run(
        f"terminal-schedule-database-{uuid4()}",
        datetime(2026, 10, 14, 16, 45, tzinfo=UTC),
    )
    context = PaperCycleContext(job_run)
    instrument_id = uuid4()
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="database-terminal-schedule-v1",
        decision_time=job_run.scheduled_for,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash="d" * 64,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="database-terminal-schedule-v1",
        decision_time=job_run.scheduled_for,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash="d" * 64,
    )
    order = Order(
        order_id=context.order_id(signal_id),
        signal_id=signal_id,
        instrument_id=instrument_id,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )
    outcome = ExecutionRejection(
        order_id=order.order_id,
        signal_id=signal_id,
        instrument_id=instrument_id,
        environment=Environment.PAPER,
        reason_code="DATABASE_SCHEDULE_REJECT",
        rejection_time=job_run.scheduled_for + timedelta(microseconds=1),
    )

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments("
                    "instrument_id, canonical_symbol, exchange, status"
                    ") VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {
                    "instrument_id": instrument_id,
                    "symbol": f"TERM-SCHEDULE-{str(instrument_id)[:8]}",
                },
            )
            assert SqlAlchemyJobRunRepository(connection).claim(job_run) is True
            PaperSignalWriter(
                SqlAlchemyPaperSignalRepository(connection)
            ).record(context, signal)
            PaperRiskWriter(
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
            PaperOrderWriter(
                SqlAlchemyPaperOrderRepository(connection)
            ).record(context, order)
            writer = PaperTerminalWriter(
                SqlAlchemyPaperTerminalRepository(connection)
            )

            with pytest.raises(
                IntegrityError,
                match="PAPER_TERMINAL_AFTER_JOB_SCHEDULE",
            ):
                with connection.begin_nested():
                    writer.record(context, outcome)
                    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

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
        finally:
            transaction.rollback()

    engine.dispose()
