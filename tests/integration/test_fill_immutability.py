import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_fills_are_append_only_execution_history() -> None:
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
            fill_id = uuid4()
            filled_at = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:id, 'FILL-IMMUTABLE', 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:id, :instrument_id, :decision_time, 'SIGNAL')"
                ),
                {"id": signal_id, "instrument_id": instrument_id, "decision_time": filled_at},
            )
            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 2)"
                ),
                {"id": order_id, "signal_id": signal_id, "instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at) "
                    "VALUES (:id, :order_id, 1, 101.25, 0.10, 0.25, :filled_at)"
                ),
                {"id": fill_id, "order_id": order_id, "filled_at": filled_at},
            )

            with pytest.raises(IntegrityError, match="FILL_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE fills SET fill_price = 99 WHERE fill_id = :id"),
                        {"id": fill_id},
                    )

            with pytest.raises(IntegrityError, match="FILL_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("DELETE FROM fills WHERE fill_id = :id"),
                        {"id": fill_id},
                    )

            stored = connection.execute(
                text(
                    "SELECT order_id, quantity, fill_price, slippage, transaction_cost, filled_at "
                    "FROM fills WHERE fill_id = :id"
                ),
                {"id": fill_id},
            ).one()
            assert stored.order_id == order_id
            assert stored.quantity == 1
            assert stored.fill_price == 101.25
            assert stored.slippage == 0.10
            assert stored.transaction_cost == 0.25
            assert stored.filled_at == filled_at
        finally:
            transaction.rollback()
