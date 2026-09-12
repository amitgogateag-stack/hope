import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_pnl_requires_durably_applied_fill() -> None:
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
            pnl_event_id = uuid4()
            event_time = datetime(2026, 9, 12, 19, 15, tzinfo=timezone.utc)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, 'PNL-APPLICATION-INTEGRITY', 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:signal_id, :instrument_id, :decision_time, 'SIGNAL')"
                ),
                {
                    "signal_id": signal_id,
                    "instrument_id": instrument_id,
                    "decision_time": event_time,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:order_id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"
                ),
                {
                    "order_id": order_id,
                    "signal_id": signal_id,
                    "instrument_id": instrument_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at) "
                    "VALUES (:fill_id, :order_id, 1, 100, 0, 0.25, :filled_at)"
                ),
                {"fill_id": fill_id, "order_id": order_id, "filled_at": event_time},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) "
                    "VALUES (:portfolio_id, 1000, 900)"
                ),
                {"portfolio_id": portfolio_id},
            )

            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO paper_portfolio_pnl_events("
                            "pnl_event_id, portfolio_id, fill_id, instrument_id, "
                            "realized_pnl_delta, commission_delta, event_time"
                            ") VALUES ("
                            ":pnl_event_id, :portfolio_id, :fill_id, :instrument_id, 0, 0.25, :event_time"
                            ")"
                        ),
                        {
                            "pnl_event_id": pnl_event_id,
                            "portfolio_id": portfolio_id,
                            "fill_id": fill_id,
                            "instrument_id": instrument_id,
                            "event_time": event_time,
                        },
                    )

            connection.execute(
                text(
                    "INSERT INTO paper_portfolio_fill_applications("
                    "portfolio_id, fill_id, application_sequence, applied_at"
                    ") VALUES (:portfolio_id, :fill_id, 1, :applied_at)"
                ),
                {
                    "portfolio_id": portfolio_id,
                    "fill_id": fill_id,
                    "applied_at": event_time,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolio_pnl_events("
                    "pnl_event_id, portfolio_id, fill_id, instrument_id, "
                    "realized_pnl_delta, commission_delta, event_time"
                    ") VALUES ("
                    ":pnl_event_id, :portfolio_id, :fill_id, :instrument_id, 0, 0.25, :event_time"
                    ")"
                ),
                {
                    "pnl_event_id": pnl_event_id,
                    "portfolio_id": portfolio_id,
                    "fill_id": fill_id,
                    "instrument_id": instrument_id,
                    "event_time": event_time,
                },
            )

            stored = connection.execute(
                text(
                    "SELECT portfolio_id, fill_id FROM paper_portfolio_pnl_events "
                    "WHERE pnl_event_id=:pnl_event_id"
                ),
                {"pnl_event_id": pnl_event_id},
            ).mappings().one()
            assert stored["portfolio_id"] == portfolio_id
            assert stored["fill_id"] == fill_id
        finally:
            transaction.rollback()
