import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperFillAccountingWriter, PaperOrderWriter, PaperSignalWriter
from hope.domain.execution import Environment, Fill, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fill_accounting import SqlAlchemyPaperFillAccountingRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository

UTC = timezone.utc
INPUTS_HASH = "f" * 64


@pytest.mark.integration
def test_paper_fill_accounting_restart_replay_is_idempotent_across_committed_connections() -> None:
    """A process restart must not duplicate a committed fill or its economic effects."""
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    portfolio_id = uuid4()
    run = create_scheduled_job_run(
        "paper-accounting-restart-replay",
        datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    )
    context = PaperCycleContext(run)
    decision = datetime(2026, 10, 1, 12, 1, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-accounting-restart-v1",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-accounting-restart-v1",
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
        Decimal("2"),
        Decimal("100"),
        Decimal("0.50"),
        Decimal("0"),
        "paper-accounting-restart-cost-v1",
        decision,
    )

    # First process/transaction: establish fully committed execution truth.
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:id, 'PAPER-RESTART-REPLAY', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run) is True
        assert PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal) is True
        assert PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order) is True
        writer = PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection))
        assert writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0) is True

    # New connection models a restarted process with no in-memory accounting state.
    with engine.begin() as connection:
        writer = PaperFillAccountingWriter(SqlAlchemyPaperFillAccountingRepository(connection))
        assert writer.record(context, portfolio_id, Decimal("1000"), fill, sequence=0) is False

        assert connection.execute(
            text("SELECT count(*) FROM fills WHERE fill_id=:id"), {"id": fill.fill_id}
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_fill_applications "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill.fill_id},
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_portfolio_pnl_events "
                "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
            ),
            {"portfolio_id": portfolio_id, "fill_id": fill.fill_id},
        ).scalar_one() == 1
        portfolio = connection.execute(
            text("SELECT cash, version FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": portfolio_id},
        ).one()
        assert portfolio == (Decimal("799.50"), 1)
        position = connection.execute(
            text(
                "SELECT quantity, average_price, realized_pnl, total_commission "
                "FROM paper_portfolio_positions "
                "WHERE portfolio_id=:portfolio_id AND instrument_id=:instrument_id"
            ),
            {"portfolio_id": portfolio_id, "instrument_id": instrument_id},
        ).one()
        assert position == (Decimal("2"), Decimal("100"), Decimal("0"), Decimal("0.50"))
