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
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


UTC = timezone.utc
INPUTS_HASH = "f" * 64


def _make_signal_order(context: PaperCycleContext, instrument_id):
    decision = datetime(2026, 9, 9, 23, 50, tzinfo=UTC)
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-fill-integrity-v1",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.8"),
        inputs_hash=INPUTS_HASH,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-fill-integrity-v1",
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
    return signal, order


def _make_fill(
    context: PaperCycleContext,
    order: Order,
    *,
    sequence: int,
    signal_id=None,
    instrument_id=None,
    side=None,
    quantity: Decimal = Decimal("1"),
    minute: int = 51,
) -> Fill:
    fill_signal_id = signal_id or order.signal_id
    return Fill(
        context.fill_id(fill_signal_id, sequence),
        order.order_id,
        fill_signal_id,
        instrument_id or order.instrument_id,
        side or order.side,
        quantity,
        Decimal("100"),
        Decimal("0.10"),
        Decimal("0.20"),
        "cost-v1",
        datetime(2026, 9, 9, 23, minute, tzinfo=UTC),
    )


@pytest.mark.integration
def test_paper_fill_must_match_tracked_order_identity() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    other_instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-fill-order-integrity",
        datetime(2026, 9, 9, 23, 55, tzinfo=UTC),
    )
    context = PaperCycleContext(run)
    signal, order = _make_signal_order(context, instrument_id)

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:first, 'PAPER-FILL-I1', 'TEST', 'ACTIVE'), "
                "(:second, 'PAPER-FILL-I2', 'TEST', 'ACTIVE')"
            ),
            {"first": instrument_id, "second": other_instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run) is True
        PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
        PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
        writer = PaperFillWriter(SqlAlchemyPaperFillRepository(connection))

        bad_signal = _make_fill(context, order, sequence=0, signal_id=uuid4())
        with pytest.raises(ValueError, match="PAPER_FILL_ORDER_IDENTITY_MISMATCH"):
            writer.record(context, bad_signal, sequence=0)

        bad_instrument = _make_fill(
            context,
            order,
            sequence=1,
            instrument_id=other_instrument_id,
        )
        with pytest.raises(ValueError, match="PAPER_FILL_ORDER_IDENTITY_MISMATCH"):
            writer.record(context, bad_instrument, sequence=1)

        bad_side = _make_fill(context, order, sequence=2, side=OrderSide.SELL)
        with pytest.raises(ValueError, match="PAPER_FILL_ORDER_IDENTITY_MISMATCH"):
            writer.record(context, bad_side, sequence=2)

        too_large = _make_fill(
            context,
            order,
            sequence=3,
            quantity=Decimal("2.01"),
        )
        with pytest.raises(ValueError, match="PAPER_FILL_QUANTITY_EXCEEDS_ORDER"):
            writer.record(context, too_large, sequence=3)

        for rejected in (bad_signal, bad_instrument, bad_side, too_large):
            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_effects "
                    "WHERE effect_type='FILL' AND entity_id=:fill_id"
                ),
                {"fill_id": rejected.fill_id},
            ).scalar_one() == 0


@pytest.mark.integration
def test_paper_fill_cumulative_quantity_cannot_exceed_order() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    run = create_scheduled_job_run(
        "paper-fill-cumulative-integrity",
        datetime(2026, 9, 9, 23, 56, tzinfo=UTC),
    )
    context = PaperCycleContext(run)
    signal, order = _make_signal_order(context, instrument_id)
    first_fill = _make_fill(
        context,
        order,
        sequence=0,
        quantity=Decimal("1.25"),
        minute=52,
    )
    overfill = _make_fill(
        context,
        order,
        sequence=1,
        quantity=Decimal("1"),
        minute=53,
    )

    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:id, 'PAPER-FILL-CUM', 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id},
        )
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run) is True
        PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection)).record(context, signal)
        PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection)).record(context, order)
        writer = PaperFillWriter(SqlAlchemyPaperFillRepository(connection))

        assert writer.record(context, first_fill, sequence=0) is True
        with pytest.raises(
            ValueError,
            match="PAPER_FILL_CUMULATIVE_QUANTITY_EXCEEDS_ORDER",
        ):
            writer.record(context, overfill, sequence=1)

        assert connection.execute(
            text("SELECT sum(quantity) FROM fills WHERE order_id=:order_id"),
            {"order_id": order.order_id},
        ).scalar_one() == Decimal("1.25")
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE effect_type='FILL' AND entity_id=:fill_id"
            ),
            {"fill_id": overfill.fill_id},
        ).scalar_one() == 0
