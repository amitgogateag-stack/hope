import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
@pytest.mark.parametrize("invalid_value", ["NaN", "Infinity", "-Infinity"])
def test_paper_portfolio_realized_pnl_delta_must_be_finite(invalid_value: str) -> None:
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
            fill_id = uuid4()
            portfolio_id = uuid4()
            fill_time = connection.execute(text("SELECT now()" )).scalar_one()

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, 'FINITE-PNL', 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:signal_id, :instrument_id, :decision_time, 'SIGNAL')"
                ),
                {"signal_id": signal_id, "instrument_id": instrument_id, "decision_time": fill_time},
            )
            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:order_id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"
                ),
                {"order_id": order_id, "signal_id": signal_id, "instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at, cost_model_version) "
                    "VALUES (:fill_id, :order_id, 1, 100, 0, 0, :filled_at, 'test')"
                ),
                {"fill_id": fill_id, "order_id": order_id, "filled_at": fill_time},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash, version) "
                    "VALUES (:portfolio_id, 1000, 900, 1)"
                ),
                {"portfolio_id": portfolio_id},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolio_fill_applications(portfolio_id, fill_id, application_sequence) "
                    "VALUES (:portfolio_id, :fill_id, 1)"
                ),
                {"portfolio_id": portfolio_id, "fill_id": fill_id},
            )

            with pytest.raises(IntegrityError, match="ck_paper_portfolio_pnl_realized_delta_finite"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO paper_portfolio_pnl_events("
                            "pnl_event_id, portfolio_id, fill_id, instrument_id, realized_pnl_delta, commission_delta, event_time"
                            ") VALUES ("
                            ":pnl_event_id, :portfolio_id, :fill_id, :instrument_id, CAST(:realized AS NUMERIC), 0, :event_time"
                            ")"
                        ),
                        {
                            "pnl_event_id": uuid4(),
                            "portfolio_id": portfolio_id,
                            "fill_id": fill_id,
                            "instrument_id": instrument_id,
                            "realized": invalid_value,
                            "event_time": fill_time,
                        },
                    )
        finally:
            transaction.rollback()
