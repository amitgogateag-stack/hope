import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import (
    PaperAccountingWriter,
    PaperCycleContext,
    PaperOrderWriter,
    PaperSignalWriter,
)
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.fills import PaperFillWriter
from hope.application.paper.portfolio_pnl import paper_portfolio_pnl_event_id
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_accounting import SqlAlchemyPaperAccountingRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc
INPUTS_HASH = "c" * 64


def persist_fill(connection, context, instrument_id, *, side, price, minute):
    decision = datetime(2026, 9, 10, 2, minute, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version=f"paper-atomic-accounting-{minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version=f"paper-atomic-accounting-{minute}",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
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
        "paper-atomic-accounting-cost-v1",
        decision,
    )
    PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    PaperFillWriter(SqlAlchemyPaperFillRepository(connection)).record(context, fill, sequence=0)
    return fill


def setup(connection, migrations_dir, *, job_key):
    apply_migrations(connection, migrations_dir)
    instrument_id = uuid4()
    portfolio_id = uuid4()
    run = create_scheduled_job_run(job_key, datetime(2026, 9, 10, 3, 0, tzinfo=UTC))
    context = PaperCycleContext(run)
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"ATOMIC-{job_key}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run) is True
    return instrument_id, portfolio_id, run, context


@pytest.mark.integration
def test_paper_accounting_atomically_persists_portfolio_and_transition_derived_pnl() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, _, context = setup(
            connection,
            migrations_dir,
            job_key="paper-atomic-accounting",
        )
        buy = persist_fill(connection, context, instrument_id, side=OrderSide.BUY, price="100", minute=50)
        sell = persist_fill(connection, context, instrument_id, side=OrderSide.SELL, price="110", minute=52)
        accounting = PaperAccountingWriter(SqlAlchemyPaperAccountingRepository(connection))

        assert accounting.apply_fill(context, portfolio_id, Decimal("1000"), buy) is True
        assert accounting.apply_fill(context, portfolio_id, Decimal("1000"), sell) is True
        assert accounting.apply_fill(context, portfolio_id, Decimal("1000"), sell) is False

        rows = connection.execute(
            text(
                "SELECT fill_id, realized_pnl_delta, commission_delta "
                "FROM paper_portfolio_pnl_events WHERE portfolio_id=:portfolio_id ORDER BY event_time"
            ),
            {"portfolio_id": portfolio_id},
        ).all()
        assert rows == [
            (buy.fill_id, Decimal("0"), Decimal("0.25")),
            (sell.fill_id, Decimal("10"), Decimal("0.25")),
        ]
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_fill_applications "
                "WHERE portfolio_id=:portfolio_id"
            ),
            {"portfolio_id": portfolio_id},
        ).scalar_one() == 2

        restored = SqlAlchemyPaperPortfolioRepository(connection).load_ledger(portfolio_id)
        assert restored is not None
        position = restored.state.positions[instrument_id]
        assert position.realized_pnl == Decimal("10")
        assert position.total_commission == Decimal("0.50")


@pytest.mark.integration
def test_paper_accounting_rolls_back_portfolio_when_pnl_persistence_conflicts() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, run, context = setup(
            connection,
            migrations_dir,
            job_key="paper-atomic-accounting-conflict",
        )
        fill = persist_fill(connection, context, instrument_id, side=OrderSide.BUY, price="100", minute=55)
        event_id = paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id)
        conflict = create_paper_effect(run, PaperEffectType.PNL, event_id, "0" * 64)
        assert SqlAlchemyPaperEffectRepository(connection).record(conflict) is True

        accounting = PaperAccountingWriter(SqlAlchemyPaperAccountingRepository(connection))
        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            accounting.apply_fill(context, portfolio_id, Decimal("500"), fill)

        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0


@pytest.mark.integration
def test_paper_accounting_rejects_applied_fill_without_matching_pnl_event() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, _, context = setup(
            connection,
            migrations_dir,
            job_key="paper-atomic-accounting-partial",
        )
        fill = persist_fill(connection, context, instrument_id, side=OrderSide.BUY, price="100", minute=57)

        legacy_two_step = SqlAlchemyPaperPortfolioRepository(connection)
        assert legacy_two_step.apply_fill_with_transition(portfolio_id, Decimal("500"), fill) is not None

        accounting = PaperAccountingWriter(SqlAlchemyPaperAccountingRepository(connection))
        with pytest.raises(RuntimeError, match="PAPER_ACCOUNTING_APPLIED_FILL_WITHOUT_PNL"):
            accounting.apply_fill(context, portfolio_id, Decimal("500"), fill)

        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
