import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_pnl_event_time_must_match_fill_time() -> None:
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
            fill_time = datetime(2026, 9, 12, 20, 30, tzinfo=timezone.utc)

            connection.execute(text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, 'PNL-TIME-INTEGRITY', 'TEST', 'ACTIVE')"), {"id": instrument_id})
            connection.execute(text("INSERT INTO signals(signal_id, instrument_id, decision_time, state) VALUES (:id, :instrument_id, :t, 'SIGNAL')"), {"id": signal_id, "instrument_id": instrument_id, "t": fill_time})
            connection.execute(text("INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"), {"id": order_id, "signal_id": signal_id, "instrument_id": instrument_id})
            connection.execute(text("INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at) VALUES (:id, :order_id, 1, 100, 0, 0.25, :t)"), {"id": fill_id, "order_id": order_id, "t": fill_time})
            connection.execute(text("INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) VALUES (:id, 1000, 900)"), {"id": portfolio_id})
            connection.execute(text("INSERT INTO paper_portfolio_fill_applications(portfolio_id, fill_id, application_sequence, applied_at) VALUES (:portfolio_id, :fill_id, 1, :t)"), {"portfolio_id": portfolio_id, "fill_id": fill_id, "t": fill_time})

            with pytest.raises(IntegrityError, match="PAPER_PORTFOLIO_PNL_EVENT_TIME_MISMATCH"):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO paper_portfolio_pnl_events(pnl_event_id, portfolio_id, fill_id, instrument_id, realized_pnl_delta, commission_delta, event_time) VALUES (:event_id, :portfolio_id, :fill_id, :instrument_id, 0, 0.25, :event_time)"), {"event_id": uuid4(), "portfolio_id": portfolio_id, "fill_id": fill_id, "instrument_id": instrument_id, "event_time": fill_time + timedelta(seconds=1)})

            event_id = uuid4()
            connection.execute(text("INSERT INTO paper_portfolio_pnl_events(pnl_event_id, portfolio_id, fill_id, instrument_id, realized_pnl_delta, commission_delta, event_time) VALUES (:event_id, :portfolio_id, :fill_id, :instrument_id, 0, 0.25, :event_time)"), {"event_id": event_id, "portfolio_id": portfolio_id, "fill_id": fill_id, "instrument_id": instrument_id, "event_time": fill_time})
            stored_time = connection.execute(text("SELECT event_time FROM paper_portfolio_pnl_events WHERE pnl_event_id=:event_id"), {"event_id": event_id}).scalar_one()
            assert stored_time == fill_time
        finally:
            transaction.rollback()
