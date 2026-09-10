import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import (
    PaperCycleContext,
    PaperFillAccountingWriter,
    PaperFillWriter,
    PaperOrderWriter,
    PaperSignalWriter,
)
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.portfolio_pnl import paper_portfolio_pnl_event_id
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_fill_accounting import SqlAlchemyPaperFillAccountingRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc
INPUTS_HASH = "d" * 64


def setup(connection, migrations_dir, *, job_key):
    apply_migrations(connection, migrations_dir)
    instrument_id = uuid4()
    portfolio_id = uuid4()
    run = create_scheduled_job_run(job_key, datetime(2026, 9, 10, 5, 0, tzinfo=UTC))
    context = PaperCycleContext(run)
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"FILL-ACCOUNT-{job_key}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run) is True
    return instrument_id, portfolio_id, run, context


def build_fill(connection, context, instrument_id, *, minute=1):
    decision = datetime(2026, 9, 10, 5, minute, tzinfo=UTC)
    strategy_version = f"paper-fill-accounting-{minute}"
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version=strategy_version,
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version=strategy_version,
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
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
        "paper-fill-accounting-cost-v1",
        decision,
    )
    PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    return fill


@pytest.mark.integration
def test_paper_fill_accounting_is_atomic_and_idempotent() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, _, context = setup(
            connection,
            migrations_dir,
            job_key="paper-fill-accounting",
        )
        fill = build_fill(connection, context, instrument_id)
        writer = PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection))

        assert writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0) is True
        assert writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0) is False

        assert connection.execute(
            text("SELECT count(*) FROM fills WHERE fill_id=:fill_id"),
            {"fill_id": fill.fill_id},
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_fill_applications "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill.fill_id},
        ).scalar_one() == 1
        row = connection.execute(
            text(
                "SELECT realized_pnl_delta, commission_delta FROM paper_portfolio_pnl_events "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill.fill_id},
        ).one()
        assert row == (Decimal("0"), Decimal("0.25"))


@pytest.mark.integration
def test_paper_fill_accounting_rejects_preexisting_fill_without_accounting() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, _, context = setup(
            connection,
            migrations_dir,
            job_key="paper-fill-accounting-partial",
        )
        fill = build_fill(connection, context, instrument_id)
        assert PaperFillWriter(SqlAlchemyPaperFillRepository(connection)).record(context, fill, sequence=0) is True

        writer = PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection))
        with pytest.raises(RuntimeError, match="PAPER_FILL_ACCOUNTING_PREEXISTING_FILL_WITHOUT_ACCOUNTING"):
            writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0)

        assert connection.execute(
            text("SELECT count(*) FROM fills WHERE fill_id=:fill_id"),
            {"fill_id": fill.fill_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0


@pytest.mark.integration
def test_paper_fill_accounting_rolls_back_new_fill_when_accounting_fails() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        instrument_id, portfolio_id, run, context = setup(
            connection,
            migrations_dir,
            job_key="paper-fill-accounting-conflict",
        )
        fill = build_fill(connection, context, instrument_id)
        event_id = paper_portfolio_pnl_event_id(portfolio_id, fill.fill_id)
        conflict = create_paper_effect(run, PaperEffectType.PNL, event_id, "0" * 64)
        assert SqlAlchemyPaperEffectRepository(connection).record(conflict) is True

        writer = PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection))
        with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_CONFLICT"):
            writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0)

        assert connection.execute(
            text("SELECT count(*) FROM fills WHERE fill_id=:fill_id"),
            {"fill_id": fill.fill_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE effect_type='FILL' AND entity_id=:fill_id"),
            {"fill_id": fill.fill_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).scalar_one() == 0
