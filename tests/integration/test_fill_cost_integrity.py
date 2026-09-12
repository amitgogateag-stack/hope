import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_fill_slippage_and_transaction_cost_must_be_nonnegative() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            instrument_id = uuid4()
            signal_id = uuid4()
            order_id = uuid4()
            fill_time = datetime(2026, 9, 12, 22, 30, tzinfo=timezone.utc)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:id, 'FILL-COST-INTEGRITY', 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:id, :instrument_id, :t, 'SIGNAL')"
                ),
                {"id": signal_id, "instrument_id": instrument_id, "t": fill_time},
            )
            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"
                ),
                {"id": order_id, "signal_id": signal_id, "instrument_id": instrument_id},
            )

            with pytest.raises(IntegrityError, match="ck_fills_slippage_nonnegative"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at) "
                            "VALUES (:fill_id, :order_id, 1, 100, -0.01, 0.25, :t)"
                        ),
                        {"fill_id": uuid4(), "order_id": order_id, "t": fill_time},
                    )

            with pytest.raises(IntegrityError, match="ck_fills_transaction_cost_nonnegative"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at) "
                            "VALUES (:fill_id, :order_id, 1, 100, 0.01, -0.25, :t)"
                        ),
                        {"fill_id": uuid4(), "order_id": order_id, "t": fill_time},
                    )

            valid_fill_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at) "
                    "VALUES (:fill_id, :order_id, 1, 100, 0, 0, :t)"
                ),
                {"fill_id": valid_fill_id, "order_id": order_id, "t": fill_time},
            )
            stored = connection.execute(
                text("SELECT slippage, transaction_cost FROM fills WHERE fill_id=:fill_id"),
                {"fill_id": valid_fill_id},
            ).mappings().one()
            assert stored["slippage"] == 0
            assert stored["transaction_cost"] == 0
        finally:
            transaction.rollback()
