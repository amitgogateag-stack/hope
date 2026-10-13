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
from hope.domain.execution.models import Environment, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


def _setup_actionable_signal(connection, migrations_dir):
    apply_migrations(connection, migrations_dir)
    instrument_id = uuid4()
    decision_time = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
    run = create_scheduled_job_run(
        f"paper-order-signal-authorization:{uuid4()}", decision_time
    )
    context = PaperCycleContext(run)
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"ORDER-SIGNAL-{instrument_id.hex[:12]}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run) is True
    signal = Signal(
        signal_id=context.signal_id(
            instrument_id=instrument_id,
            strategy_version="paper-order-signal-v1",
            decision_time=decision_time,
            signal_type=SignalType.ENTRY,
            conviction=Decimal("0.8"),
            inputs_hash="a" * 64,
        ),
        instrument_id=instrument_id,
        strategy_version="paper-order-signal-v1",
        decision_time=decision_time,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash="a" * 64,
    )
    assert PaperSignalWriter(
        SqlAlchemyPaperSignalRepository(connection)
    ).record(context, signal) is True
    return context, signal


@pytest.mark.integration
def test_canonical_paper_order_preserves_source_signal_authorization() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        context, signal = _setup_actionable_signal(connection, migrations_dir)
        order = Order(
            order_id=context.order_id(signal.signal_id),
            signal_id=signal.signal_id,
            instrument_id=signal.instrument_id,
            side=OrderSide.BUY,
            quantity=Decimal("1.25"),
            environment=Environment.PAPER,
            signal_type=SignalType.ENTRY,
        )
        assert PaperOrderWriter(
            SqlAlchemyPaperOrderRepository(connection)
        ).record(context, order) is True

    engine.dispose()


@pytest.mark.integration
def test_paper_order_signal_type_mismatch_is_rejected_before_persistence() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        _, signal = _setup_actionable_signal(connection, migrations_dir)
        order_id = uuid4()
        with pytest.raises(IntegrityError, match="PAPER_ORDER_SIGNAL_TYPE_MISMATCH"):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO orders(order_id, signal_id, instrument_id, "
                        "environment, side, quantity, signal_type) VALUES ("
                        ":order_id, :signal_id, :instrument_id, 'PAPER', 'SELL', 1, 'EXIT')"
                    ),
                    {
                        "order_id": order_id,
                        "signal_id": signal.signal_id,
                        "instrument_id": signal.instrument_id,
                    },
                )
        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE order_id=:order_id"),
            {"order_id": order_id},
        ).scalar_one() == 0

    engine.dispose()


@pytest.mark.integration
def test_paper_order_non_actionable_source_signal_is_rejected() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        instrument_id = uuid4()
        signal_id = uuid4()
        order_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id, "symbol": f"NON-ACTIONABLE-{instrument_id.hex[:12]}"},
        )
        connection.execute(
            text(
                "INSERT INTO signals(signal_id, instrument_id, decision_time, state, "
                "strategy_version, signal_type, conviction, inputs_hash) VALUES ("
                ":signal_id, :instrument_id, :decision_time, 'NO_SIGNAL', "
                "'paper-order-signal-v1', 'ENTRY', 0.8, :inputs_hash)"
            ),
            {
                "signal_id": signal_id,
                "instrument_id": instrument_id,
                "decision_time": datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc),
                "inputs_hash": "b" * 64,
            },
        )
        with pytest.raises(
            IntegrityError,
            match="PAPER_ORDER_SOURCE_SIGNAL_NOT_ACTIONABLE",
        ):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO orders(order_id, signal_id, instrument_id, "
                        "environment, side, quantity, signal_type) VALUES ("
                        ":order_id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1, 'ENTRY')"
                    ),
                    {
                        "order_id": order_id,
                        "signal_id": signal_id,
                        "instrument_id": instrument_id,
                    },
                )
        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE order_id=:order_id"),
            {"order_id": order_id},
        ).scalar_one() == 0

    engine.dispose()
