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
    PaperEffectType,
    PaperOrderWriter,
    PaperRiskWriter,
    PaperSignalWriter,
    create_paper_effect,
    paper_fill_payload_hash,
)
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
def test_postgres_rejects_future_fill_through_direct_accounting_repository() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    job_run = create_scheduled_job_run(
        f"fill-schedule-database-{uuid4()}",
        datetime(2026, 10, 14, 15, 45, tzinfo=UTC),
    )
    context = PaperCycleContext(job_run)
    instrument_id = uuid4()
    portfolio_id = uuid4()
    decision_time = job_run.scheduled_for
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="database-fill-schedule-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash="c" * 64,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="database-fill-schedule-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash="c" * 64,
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
    fill = Fill(
        context.fill_id(signal_id, 0),
        order.order_id,
        signal_id,
        instrument_id,
        OrderSide.BUY,
        Decimal("1"),
        Decimal("100"),
        Decimal("0.25"),
        Decimal("0"),
        "database-fill-schedule-cost-v1",
        job_run.scheduled_for + timedelta(microseconds=1),
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
                    "symbol": f"FILL-SCHEDULE-{str(instrument_id)[:8]}",
                },
            )
            assert SqlAlchemyJobRunRepository(connection).claim(job_run) is True
            PaperSignalWriter(
                SqlAlchemyPaperSignalRepository(connection)
            ).record(context, signal)
            PaperOrderWriter(
                SqlAlchemyPaperOrderRepository(connection)
            ).record(context, order)
            PaperRiskWriter(
                SqlAlchemyPaperRiskRepository(connection)
            ).record(
                context,
                RiskAssessment(
                    signal_id=signal_id,
                    decision=RiskDecision.APPROVE,
                    reason_code="TEST_APPROVED",
                    approved_quantity=fill.quantity,
                ),
            )
            effect = create_paper_effect(
                context.job_run,
                PaperEffectType.FILL,
                fill.fill_id,
                paper_fill_payload_hash(fill),
            )
            repository = SqlAlchemyPaperFillRepository(connection)

            with pytest.raises(
                IntegrityError,
                match="PAPER_FILL_AFTER_JOB_SCHEDULE",
            ):
                with connection.begin_nested():
                    repository.persist(effect, fill)
                    connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

            assert connection.execute(
                text("SELECT count(*) FROM fills WHERE fill_id=:fill_id"),
                {"fill_id": fill.fill_id},
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_portfolio_fill_applications "
                    "WHERE portfolio_id=:portfolio_id"
                ),
                {"portfolio_id": portfolio_id},
            ).scalar_one() == 0
        finally:
            transaction.rollback()

    engine.dispose()
