import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperFillWriter, PaperOrderWriter, PaperSignalWriter
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc
INPUTS_HASH = "f" * 64


def persist_fill(connection, context, instrument_id, *, side, quantity, price, sequence, decision_minute):
    decision = datetime(2026, 9, 9, 23, decision_minute, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version=f"paper-portfolio-{decision_minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version=f"paper-portfolio-{decision_minute}",
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
        quantity=quantity,
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )
    fill = Fill(
        context.fill_id(signal_id, sequence),
        order.order_id,
        signal_id,
        instrument_id,
        side,
        quantity,
        price,
        Decimal("0.25") if side is OrderSide.BUY else Decimal("0.20"),
        Decimal("0.10"),
        "portfolio-cost-v1",
        decision.replace(minute=decision_minute + 1),
    )
    PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    PaperFillWriter(SqlAlchemyPaperFillRepository(connection)).record(context, fill, sequence=sequence)
    return fill


@pytest.mark.integration
def test_paper_portfolio_materializes_full_state_and_restores_applied_fill_history():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id, portfolio_id = uuid4(), uuid4()
    run = create_scheduled_job_run("paper-portfolio", datetime(2026, 9, 9, 23, 20, tzinfo=UTC))
    context = PaperCycleContext(run)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id,'PAPER-PORT','TEST','ACTIVE')"),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        buy = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.BUY, quantity=Decimal("2"), price=Decimal("101.5"), sequence=0, decision_minute=21,
        )
        sell = persist_fill(
            connection, context, instrument_id,
            side=OrderSide.SELL, quantity=Decimal("1"), price=Decimal("110"), sequence=0, decision_minute=23,
        )
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        assert repository.apply_fill(portfolio_id, Decimal("1000"), buy)
        assert repository.apply_fill(portfolio_id, Decimal("1000"), sell)
        assert repository.apply_fill(portfolio_id, Decimal("1000"), sell) is False

        restored = repository.load_ledger(portfolio_id)
        assert restored is not None
        position = restored.state.positions[instrument_id]
        assert restored.state.cash == Decimal("906.55")
        assert position.quantity == Decimal("1")
        assert position.average_price == Decimal("101.5")
        assert position.realized_pnl == Decimal("8.5")
        assert position.total_commission == Decimal("0.45")
        assert restored.applied_fill_ids == frozenset({buy.fill_id, sell.fill_id})
        assert connection.execute(
            text("SELECT version FROM paper_portfolios WHERE portfolio_id=:id"), {"id": portfolio_id}
        ).scalar_one() == 2


@pytest.mark.integration
def test_paper_portfolio_rejects_untracked_or_conflicting_fill_without_state_mutation():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    portfolio_id, instrument_id = uuid4(), uuid4()
    rogue = Fill(
        uuid4(), uuid4(), uuid4(), instrument_id, OrderSide.BUY,
        Decimal("1"), Decimal("50"), Decimal("0"), Decimal("0"), "rogue-cost-v1",
        datetime(2026, 9, 9, 23, 30, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        with pytest.raises(ValueError, match="PAPER_PORTFOLIO_FILL_UNTRACKED"):
            repository.apply_fill(portfolio_id, Decimal("500"), rogue)
        assert connection.execute(text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"), {"id": portfolio_id}).scalar_one() == 0
