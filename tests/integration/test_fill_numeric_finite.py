import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("fill_price", "ck_fills_fill_price_finite"),
        ("slippage", "ck_fills_slippage_finite"),
        ("transaction_cost", "ck_fills_transaction_cost_finite"),
    ],
)
@pytest.mark.parametrize("invalid_value", ["NaN", "Infinity", "-Infinity"])
def test_fill_economic_numerics_must_be_finite(
    column: str,
    constraint: str,
    invalid_value: str,
) -> None:
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
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, 'FINITE-FILL', 'TEST', 'ACTIVE')"
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
            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:order_id, :signal_id, :instrument_id, 'BACKTEST', 'BUY', 10)"
                ),
                {
                    "order_id": order_id,
                    "signal_id": signal_id,
                    "instrument_id": instrument_id,
                },
            )

            values = {
                "fill_price": "100",
                "slippage": "0",
                "transaction_cost": "0",
            }
            values[column] = invalid_value

            with pytest.raises(IntegrityError, match=constraint):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at) "
                            "VALUES (:fill_id, :order_id, 1, CAST(:fill_price AS NUMERIC), "
                            "CAST(:slippage AS NUMERIC), CAST(:transaction_cost AS NUMERIC), now())"
                        ),
                        {
                            "fill_id": uuid4(),
                            "order_id": order_id,
                            **values,
                        },
                    )

            connection.execute(
                text(
                    "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at) "
                    "VALUES (:fill_id, :order_id, 1, 100, 0, 0, now())"
                ),
                {"fill_id": uuid4(), "order_id": order_id},
            )
        finally:
            transaction.rollback()
