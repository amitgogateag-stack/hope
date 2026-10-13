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
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.application.paper.orders import paper_order_payload_hash
from hope.domain.execution.models import Environment, Order, OrderSide
from hope.domain.signal.models import Signal, SignalType
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


def _setup_lineage(connection, migrations_dir):
    apply_migrations(connection, migrations_dir)
    instrument_id = uuid4()
    scheduled_for = datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc)
    run = create_scheduled_job_run(f"paper-order-payload:{uuid4()}", scheduled_for)
    context = PaperCycleContext(run)
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"id": instrument_id, "symbol": f"ORDER-PAYLOAD-{instrument_id.hex[:12]}"},
    )
    assert SqlAlchemyJobRunRepository(connection).claim(run) is True
    signal_id = context.signal_id(
        instrument_id=instrument_id,
        strategy_version="paper-order-payload-v1",
        decision_time=scheduled_for,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash="d" * 64,
    )
    signal = Signal(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version="paper-order-payload-v1",
        decision_time=scheduled_for,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.75"),
        inputs_hash="d" * 64,
    )
    assert PaperSignalWriter(
        SqlAlchemyPaperSignalRepository(connection)
    ).record(context, signal) is True
    order = Order(
        order_id=context.order_id(signal_id),
        signal_id=signal_id,
        instrument_id=instrument_id,
        side=OrderSide.BUY,
        quantity=Decimal("2.5000"),
        environment=Environment.PAPER,
        signal_type=SignalType.ENTRY,
    )
    return run, context, order


@pytest.mark.integration
def test_paper_order_effect_payload_is_database_authenticated() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        _, context, order = _setup_lineage(connection, migrations_dir)
        assert PaperOrderWriter(
            SqlAlchemyPaperOrderRepository(connection)
        ).record(context, order) is True
        row = connection.execute(
            text(
                "SELECT effect.payload_hash, hope_paper_order_payload_hash("
                "source_order.order_id, source_order.signal_id, "
                "source_order.instrument_id, source_order.side, "
                "source_order.quantity, source_order.environment, "
                "source_order.signal_type) AS database_hash "
                "FROM orders source_order JOIN paper_effects effect "
                "ON effect.effect_type='ORDER' "
                "AND effect.entity_id=source_order.order_id "
                "WHERE source_order.order_id=:order_id"
            ),
            {"order_id": order.order_id},
        ).one()
        assert row.payload_hash == paper_order_payload_hash(order)
        assert row.database_hash == row.payload_hash

    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("effect_first", [True, False])
def test_conflicting_paper_order_pair_rolls_back_in_either_insert_order(
    effect_first: bool,
) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.begin() as connection:
        run, _, order = _setup_lineage(connection, migrations_dir)
        bad_effect = create_paper_effect(
            run,
            PaperEffectType.ORDER,
            order.order_id,
            "f" * 64,
        )

        def insert_effect():
            connection.execute(
                text(
                    "INSERT INTO paper_effects("
                    "effect_id, job_run_id, effect_type, entity_id, payload_hash"
                    ") VALUES ("
                    ":effect_id, :job_run_id, 'ORDER', :order_id, :payload_hash"
                    ")"
                ),
                {
                    "effect_id": bad_effect.effect_id,
                    "job_run_id": run.job_run_id,
                    "order_id": order.order_id,
                    "payload_hash": bad_effect.payload_hash,
                },
            )

        def insert_order():
            connection.execute(
                text(
                    "INSERT INTO orders("
                    "order_id, signal_id, instrument_id, environment, side, "
                    "quantity, signal_type"
                    ") VALUES ("
                    ":order_id, :signal_id, :instrument_id, 'PAPER', 'BUY', "
                    ":quantity, 'ENTRY'"
                    ")"
                ),
                {
                    "order_id": order.order_id,
                    "signal_id": order.signal_id,
                    "instrument_id": order.instrument_id,
                    "quantity": order.quantity,
                },
            )

        with pytest.raises(
            IntegrityError,
            match="PAPER_ORDER_EFFECT_PAYLOAD_MISMATCH",
        ):
            with connection.begin_nested():
                first, second = (
                    (insert_effect, insert_order)
                    if effect_first
                    else (insert_order, insert_effect)
                )
                first()
                second()

        assert connection.execute(
            text("SELECT count(*) FROM orders WHERE order_id=:order_id"),
            {"order_id": order.order_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT count(*) FROM paper_effects "
                "WHERE effect_type='ORDER' AND entity_id=:order_id"
            ),
            {"order_id": order.order_id},
        ).scalar_one() == 0

    engine.dispose()
