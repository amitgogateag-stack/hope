import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperFillWriter, PaperOrderWriter, PaperSignalWriter
from hope.application.paper.portfolio_pnl import PaperPortfolioPnLWriter
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.paper_portfolio_pnl import SqlAlchemyPaperPortfolioPnLRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc
INPUTS_HASH = "a" * 64


def persist_fill(connection, context, instrument_id, side, price, decision_minute):
    decision = datetime(2026, 9, 10, 0, decision_minute, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version=f"paper-accounting-{decision_minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version=f"paper-accounting-{decision_minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    order = Order(
        order_id=context.order_id(signal_id),
        signal_id=signal_id,
        instrument_id=instrument_id,
        side=side,
        quantity=Decimal("1"),
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )
    fill = Fill(
        context.fill_id(signal_id, 0),
        order.order_id,
        signal_id,
        instrument_id,
        side,
        Decimal("1"),
        Decimal(price),
        Decimal("0.25"),
        Decimal("0"),
        "paper-accounting-cost-v1",
        decision,
    )
    PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    PaperFillWriter(SqlAlchemyPaperFillRepository(connection)).record(context, fill, sequence=0)
    return fill


@pytest.mark.integration
def test_paper_portfolio_pnl_persists_only_transition_derived_accounting() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run("paper-portfolio-pnl", datetime(2026, 9, 10, 0, 0, tzinfo=UTC))
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-PNL-PORT','TEST','ACTIVE')"),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)

        buy = persist_fill(connection, context, instrument_id, OrderSide.BUY, "100", 1)
        sell = persist_fill(connection, context, instrument_id, OrderSide.SELL, "110", 2)
        portfolio = SqlAlchemyPaperPortfolioRepository(connection)
        assert portfolio.apply_fill_with_transition(portfolio_id, Decimal("1000"), buy) is not None
        sell_transition = portfolio.apply_fill_with_transition(portfolio_id, Decimal("1000"), sell)
        assert sell_transition is not None
        assert sell_transition.realized_pnl_delta == Decimal("10")
        assert sell_transition.commission_delta == Decimal("0.25")

        writer = PaperPortfolioPnLWriter(SqlAlchemyPaperPortfolioPnLRepository(connection))
        assert writer.record(context, portfolio_id, sell, sell_transition) is True
        assert writer.record(context, portfolio_id, sell, sell_transition) is False

        row = connection.execute(
            text(
                "SELECT portfolio_id, fill_id, instrument_id, realized_pnl_delta, commission_delta "
                "FROM paper_portfolio_pnl_events WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": sell.fill_id},
        ).mappings().one()
        assert row["portfolio_id"] == portfolio_id
        assert row["fill_id"] == sell.fill_id
        assert row["instrument_id"] == instrument_id
        assert row["realized_pnl_delta"] == Decimal("10")
        assert row["commission_delta"] == Decimal("0.25")
