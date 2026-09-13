import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_application_rejects_non_paper_fill() -> None:
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
            backtest_signal_id = uuid4()
            paper_signal_id = uuid4()
            backtest_order_id = uuid4()
            paper_order_id = uuid4()
            backtest_fill_id = uuid4()
            paper_fill_id = uuid4()
            portfolio_id = uuid4()
            event_time = datetime(2026, 9, 13, 5, 0, tzinfo=timezone.utc)

            connection.execute(
                text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, 'PAPER-ENV-INTEGRITY', 'TEST', 'ACTIVE')"),
                {"id": instrument_id},
            )
            connection.execute(
                text("INSERT INTO signals(signal_id, instrument_id, decision_time, state) VALUES (:id, :instrument_id, :t, 'SIGNAL')"),
                {"id": backtest_signal_id, "instrument_id": instrument_id, "t": event_time},
            )
            connection.execute(
                text("INSERT INTO signals(signal_id, instrument_id, decision_time, state) VALUES (:id, :instrument_id, :t, 'SIGNAL')"),
                {"id": paper_signal_id, "instrument_id": instrument_id, "t": event_time},
            )
            connection.execute(
                text("INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) VALUES (:id, :signal_id, :instrument_id, 'BACKTEST', 'BUY', 1)"),
                {"id": backtest_order_id, "signal_id": backtest_signal_id, "instrument_id": instrument_id},
            )
            connection.execute(
                text("INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"),
                {"id": paper_order_id, "signal_id": paper_signal_id, "instrument_id": instrument_id},
            )
            connection.execute(
                text("INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at, cost_model_version) VALUES (:id, :order_id, 1, 100, 0, 0, :t, 'test-cost-v1')"),
                {"id": backtest_fill_id, "order_id": backtest_order_id, "t": event_time},
            )
            connection.execute(
                text("INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at, cost_model_version) VALUES (:id, :order_id, 1, 100, 0, 0, :t, 'test-cost-v1')"),
                {"id": paper_fill_id, "order_id": paper_order_id, "t": event_time},
            )
            connection.execute(
                text("INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) VALUES (:id, 1000, 1000)"),
                {"id": portfolio_id},
            )

            with pytest.raises(IntegrityError, match="PAPER_PORTFOLIO_FILL_ENVIRONMENT_MISMATCH"):
                with connection.begin_nested():
                    connection.execute(
                        text("INSERT INTO paper_portfolio_fill_applications(portfolio_id, fill_id, application_sequence, applied_at) VALUES (:portfolio_id, :fill_id, 1, :t)"),
                        {"portfolio_id": portfolio_id, "fill_id": backtest_fill_id, "t": event_time},
                    )

            connection.execute(
                text("INSERT INTO paper_portfolio_fill_applications(portfolio_id, fill_id, application_sequence, applied_at) VALUES (:portfolio_id, :fill_id, 1, :t)"),
                {"portfolio_id": portfolio_id, "fill_id": paper_fill_id, "t": event_time},
            )
            stored_fill_id = connection.execute(
                text("SELECT fill_id FROM paper_portfolio_fill_applications WHERE portfolio_id=:portfolio_id"),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
            assert stored_fill_id == paper_fill_id
        finally:
            transaction.rollback()
