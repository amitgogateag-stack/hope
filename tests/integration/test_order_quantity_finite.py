import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
@pytest.mark.parametrize("invalid_quantity", ["NaN", "Infinity", "-Infinity"])
def test_order_quantity_must_be_finite(invalid_quantity: str) -> None:
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
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, 'FINITE-QTY', 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:signal_id, :instrument_id, now(), 'SIGNAL')"
                ),
                {"signal_id": signal_id, "instrument_id": instrument_id},
            )

            with pytest.raises(IntegrityError, match="ck_orders_quantity_finite"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                            "VALUES (:order_id, :signal_id, :instrument_id, 'PAPER', 'BUY', CAST(:quantity AS NUMERIC))"
                        ),
                        {
                            "order_id": uuid4(),
                            "signal_id": signal_id,
                            "instrument_id": instrument_id,
                            "quantity": invalid_quantity,
                        },
                    )

            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:order_id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"
                ),
                {
                    "order_id": uuid4(),
                    "signal_id": signal_id,
                    "instrument_id": instrument_id,
                },
            )
        finally:
            transaction.rollback()
