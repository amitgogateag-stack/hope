import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_pnl_instrument_must_match_applied_fill_order() -> None:
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
            wrong_instrument_id = uuid4()
            signal_id = uuid4()
            order_id = uuid4()
            fill_id = uuid4()
            portfolio_id = uuid4()
            event_time = datetime(2026, 9, 12, 19, 45, tzinfo=timezone.utc)

            connection.execute(
                text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, 'PNL-INSTRUMENT', 'TEST', 'ACTIVE'), (:wrong_id, 'PNL-INSTRUMENT-WRONG', 'TEST', 'ACTIVE')"),
                {"id": instrument_id, "wrong_id": wrong_instrument_id},
            )
            connection.execute(
                text("INSERT INTO signals(signal_id, instrument_id, decision_time, state) VALUES (:signal_id, :instrument_id, :event_time, 'SIGNAL')"),
                {"signal_id": signal_id, "instrument_id": instrument_id, "event_time": event_time},
            )
            connection.execute(
                text("INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) VALUES (:order_id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"),
                {"order_id": order_id, "signal_id": signal_id, "instrument_id": instrument_id},
            )
            connection.execute(
                text("INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at) VALUES (:fill_id, :order_id, 1, 100, 0, 0.25, :event_time)"),
                {"fill_id": fill_id, "order_id": order_id, "event_time": event_time},
            )
            connection.execute(
                text("INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) VALUES (:portfolio_id, 1000, 900)"),
                {"portfolio_id": portfolio_id},
            )
            connection.execute(
                text("INSERT INTO paper_portfolio_fill_applications(portfolio_id, fill_id, application_sequence, applied_at) VALUES (:portfolio_id, :fill_id, 1, :event_time)"),
                {"portfolio_id": portfolio_id, "fill_id": fill_id, "event_time": event_time},
            )

            with pytest.raises(IntegrityError, match="PAPER_PORTFOLIO_PNL_INSTRUMENT_MISMATCH"):
                with connection.begin_nested():
                    connection.execute(
                        text("INSERT INTO paper_portfolio_pnl_events(pnl_event_id, portfolio_id, fill_id, instrument_id, realized_pnl_delta, commission_delta, event_time) VALUES (:event_id, :portfolio_id, :fill_id, :instrument_id, 0, 0.25, :event_time)"),
                        {"event_id": uuid4(), "portfolio_id": portfolio_id, "fill_id": fill_id, "instrument_id": wrong_instrument_id, "event_time": event_time},
                    )

            connection.execute(
                text("INSERT INTO paper_portfolio_pnl_events(pnl_event_id, portfolio_id, fill_id, instrument_id, realized_pnl_delta, commission_delta, event_time) VALUES (:event_id, :portfolio_id, :fill_id, :instrument_id, 0, 0.25, :event_time)"),
                {"event_id": uuid4(), "portfolio_id": portfolio_id, "fill_id": fill_id, "instrument_id": instrument_id, "event_time": event_time},
            )
        finally:
            transaction.rollback()
