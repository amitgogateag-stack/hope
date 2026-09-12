import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_orders_are_append_only_intent_history() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            instrument_id = uuid4()
            signal_id = uuid4()
            order_id = uuid4()
            decision_time = datetime(2026, 9, 12, 16, 15, tzinfo=UTC)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:id, 'ORDER-IMMUTABLE', 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:id, :instrument_id, :decision_time, 'SIGNAL')"
                ),
                {"id": signal_id, "instrument_id": instrument_id, "decision_time": decision_time},
            )
            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 2)"
                ),
                {"id": order_id, "signal_id": signal_id, "instrument_id": instrument_id},
            )

            with pytest.raises(IntegrityError, match="ORDER_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE orders SET quantity = 3 WHERE order_id = :id"),
                        {"id": order_id},
                    )

            with pytest.raises(IntegrityError, match="ORDER_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("DELETE FROM orders WHERE order_id = :id"),
                        {"id": order_id},
                    )

            stored = connection.execute(
                text(
                    "SELECT signal_id, instrument_id, environment, side, quantity "
                    "FROM orders WHERE order_id = :id"
                ),
                {"id": order_id},
            ).one()
            assert stored.signal_id == signal_id
            assert stored.instrument_id == instrument_id
            assert stored.environment == "PAPER"
            assert stored.side == "BUY"
            assert stored.quantity == Decimal("2")
        finally:
            transaction.rollback()
