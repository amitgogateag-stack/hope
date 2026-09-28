import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext, PaperOrderWriter, PaperSignalWriter
from hope.application.paper.fills import PaperFillWriter
from hope.application.paper.terminals import PaperTerminalWriter
from hope.domain.execution.models import Environment, ExecutionCancellation, ExecutionRejection, Order, OrderSide
from hope.domain.execution.fills import Fill
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
from hope.infrastructure.repositories.paper_terminals import SqlAlchemyPaperTerminalRepository

UTC = timezone.utc
INPUTS_HASH = "f" * 64


def _setup_order(connection, run, instrument_id):
    context = PaperCycleContext(run)
    decision = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-terminal-v1",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-terminal-v1",
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
        {"id": instrument_id, "symbol": f"TERM-{str(instrument_id)[:8]}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run)
    assert PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
    assert PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
    return context, signal, order


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["CANCELLED", "REJECTED"])
def test_paper_terminal_outcome_is_durable_idempotent_and_restart_readable(kind):
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        f"paper-terminal-{kind.lower()}",
        datetime(2026, 9, 9, 20, 5, tzinfo=UTC),
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        if kind == "CANCELLED":
            outcome = ExecutionCancellation(
                order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
                "TEST_CANCEL", datetime(2026, 9, 9, 20, 1, tzinfo=UTC), Decimal("2"),
            )
        else:
            outcome = ExecutionRejection(
                order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
                "TEST_REJECT", datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
            )
        writer = PaperTerminalWriter(SqlAlchemyPaperTerminalRepository(connection))
        assert writer.record(context, outcome)
        assert writer.record(context, outcome) is False
        assert connection.execute(
            text("SELECT count(*) FROM paper_order_terminal_events WHERE order_id=:id"),
            {"id": order.order_id},
        ).scalar_one() == 1

    # A fresh transaction/repository must reconstruct the same terminal truth.
    with engine.begin() as connection:
        restored = SqlAlchemyPaperTerminalRepository(connection).get(order.order_id)
        assert restored == outcome
        effect_type = connection.execute(
            text("SELECT effect_type FROM paper_effects WHERE entity_id=:id"),
            {"id": order.order_id},
        ).scalar_one()
        assert effect_type == kind.replace("CANCELLED", "CANCELLATION").replace("REJECTED", "REJECTION")
    engine.dispose()


@pytest.mark.integration
def test_paper_terminal_history_is_immutable():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run("paper-terminal-immutable", datetime(2026, 9, 9, 20, 5, tzinfo=UTC))

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        outcome = ExecutionRejection(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "TEST_REJECT", datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        assert PaperTerminalWriter(SqlAlchemyPaperTerminalRepository(connection)).record(context, outcome)
        with pytest.raises(IntegrityError, match="PAPER_ORDER_TERMINAL_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE paper_order_terminal_events SET reason_code='FORGED' WHERE order_id=:id"),
                    {"id": order.order_id},
                )
        with pytest.raises(IntegrityError, match="PAPER_ORDER_TERMINAL_IMMUTABLE"):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM paper_order_terminal_events WHERE order_id=:id"),
                    {"id": order.order_id},
                )
    engine.dispose()


@pytest.mark.integration
def test_paper_rejection_after_fill_fails_closed_without_terminal_effect():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run("paper-terminal-filled-reject", datetime(2026, 9, 9, 20, 5, tzinfo=UTC))

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        context, signal, order = _setup_order(connection, run, instrument_id)
        fill = Fill(
            context.fill_id(signal.signal_id, 0), order.order_id, signal.signal_id,
            instrument_id, OrderSide.BUY, Decimal("1"), Decimal("100"),
            Decimal("0.10"), Decimal("0.05"), "cost-v1",
            datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
        )
        assert PaperFillWriter(SqlAlchemyPaperFillRepository(connection)).record(context, fill, sequence=0)
        outcome = ExecutionRejection(
            order.order_id, signal.signal_id, instrument_id, Environment.PAPER,
            "LATE_REJECT", datetime(2026, 9, 9, 20, 2, tzinfo=UTC),
        )
        with pytest.raises(IntegrityError, match="PAPER_REJECTION_REQUIRES_UNFILLED_ORDER"):
            PaperTerminalWriter(SqlAlchemyPaperTerminalRepository(connection)).record(context, outcome)
        assert connection.execute(
            text("SELECT count(*) FROM paper_order_terminal_events WHERE order_id=:id"),
            {"id": order.order_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM paper_effects WHERE entity_id=:id AND effect_type='REJECTION'"),
            {"id": order.order_id},
        ).scalar_one() == 0
    engine.dispose()
