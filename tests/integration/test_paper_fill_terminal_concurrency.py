import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperOrderWriter, PaperSignalWriter
from hope.application.paper.fills import PaperFillWriter
from hope.application.paper.terminals import PaperTerminalWriter
from hope.domain.execution import Fill
from hope.domain.execution.models import Environment, ExecutionRejection, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
from hope.infrastructure.repositories.paper_terminals import SqlAlchemyPaperTerminalRepository

UTC = timezone.utc
INPUTS_HASH = "e" * 64


def _setup_order(connection, run, instrument_id):
    context = PaperCycleContext(run)
    decision = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-concurrency-v1",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-concurrency-v1",
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
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"CONC-{str(instrument_id)[:8]}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run)
    assert PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    assert PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    return context, signal, order


@pytest.mark.integration
def test_paper_terminal_write_serializes_behind_uncommitted_fill():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-fill-first-terminal-serialization",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as setup_connection:
        apply_migrations(setup_connection, migrations_dir)
        context, signal, order = _setup_order(setup_connection, run, instrument_id)

    fill_connection = engine.connect()
    terminal_connection = engine.connect()
    fill_transaction = fill_connection.begin()
    try:
        fill = Fill(
            context.fill_id(signal.signal_id, 0),
            order.order_id,
            signal.signal_id,
            instrument_id,
            OrderSide.BUY,
            Decimal("1"),
            Decimal("100"),
            Decimal("0.10"),
            Decimal("0.05"),
            "cost-v1",
            datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        assert PaperFillWriter(
            SqlAlchemyPaperFillRepository(fill_connection)
        ).record(context, fill, sequence=0)

        terminal_transaction = terminal_connection.begin()
        try:
            terminal_connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
            rejection = ExecutionRejection(
                order.order_id,
                signal.signal_id,
                instrument_id,
                Environment.PAPER,
                "CONCURRENT_REJECT",
                datetime(2026, 9, 9, 20, 2, tzinfo=UTC),
            )
            with pytest.raises(OperationalError, match="lock timeout"):
                PaperTerminalWriter(
                    SqlAlchemyPaperTerminalRepository(terminal_connection)
                ).record(context, rejection)
        finally:
            terminal_transaction.rollback()

        fill_transaction.commit()

        with terminal_connection.begin():
            with pytest.raises(
                IntegrityError,
                match="PAPER_REJECTION_REQUIRES_UNFILLED_ORDER",
            ):
                PaperTerminalWriter(
                    SqlAlchemyPaperTerminalRepository(terminal_connection)
                ).record(context, rejection)
            assert terminal_connection.execute(
                text(
                    "SELECT count(*) FROM paper_order_terminal_events "
                    "WHERE order_id=:id"
                ),
                {"id": order.order_id},
            ).scalar_one() == 0
            assert terminal_connection.execute(
                text(
                    "SELECT count(*) FROM paper_effects "
                    "WHERE entity_id=:id AND effect_type='REJECTION'"
                ),
                {"id": order.order_id},
            ).scalar_one() == 0
    finally:
        if fill_transaction.is_active:
            fill_transaction.rollback()
        fill_connection.close()
        terminal_connection.close()
        engine.dispose()
