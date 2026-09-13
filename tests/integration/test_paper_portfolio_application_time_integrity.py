import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_fill_applications_reject_fill_time_regression() -> None:
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
            first_fill_id = uuid4()
            second_fill_id = uuid4()
            portfolio_id = uuid4()
            earlier = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
            later = earlier + timedelta(minutes=1)

            connection.execute(text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, 'PAPER-TIME', 'TEST', 'ACTIVE')"), {"id": instrument_id})
            connection.execute(text("INSERT INTO signals(signal_id, instrument_id, decision_time, state) VALUES (:id, :instrument_id, :t, 'SIGNAL')"), {"id": signal_id, "instrument_id": instrument_id, "t": earlier})
            connection.execute(text("INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 2)"), {"id": order_id, "signal_id": signal_id, "instrument_id": instrument_id})
            connection.execute(text("INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at, cost_model_version) VALUES (:id, :order_id, 1, 100, 0, 0, :t, 'test-v1')"), {"id": first_fill_id, "order_id": order_id, "t": later})
            connection.execute(text("INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at, cost_model_version) VALUES (:id, :order_id, 1, 100, 0, 0, :t, 'test-v1')"), {"id": second_fill_id, "order_id": order_id, "t": earlier})
            connection.execute(text("INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) VALUES (:id, 1000, 1000)"), {"id": portfolio_id})
            connection.execute(text("INSERT INTO paper_portfolio_fill_applications(portfolio_id, fill_id, application_sequence, applied_at) VALUES (:portfolio_id, :fill_id, 1, :t)"), {"portfolio_id": portfolio_id, "fill_id": first_fill_id, "t": later})

            with pytest.raises(IntegrityError, match="PAPER_PORTFOLIO_APPLICATION_TIME_REGRESSION"):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO paper_portfolio_fill_applications(portfolio_id, fill_id, application_sequence, applied_at) VALUES (:portfolio_id, :fill_id, 2, :t)"), {"portfolio_id": portfolio_id, "fill_id": second_fill_id, "t": earlier})
        finally:
            transaction.rollback()
