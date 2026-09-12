import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_fill_applications_are_append_only_history() -> None:
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
            portfolio_id = uuid4()
            event_time = datetime(2026, 9, 12, 18, 15, tzinfo=UTC)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:id, 'PAPER-APPLICATION-IMMUTABLE', 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:id, :instrument_id, :decision_time, 'SIGNAL')"
                ),
                {"id": signal_id, "instrument_id": instrument_id, "decision_time": event_time},
            )
            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"
                ),
                {"id": order_id, "signal_id": signal_id, "instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO fills(fill_id, order_id, quantity, fill_price, filled_at) "
                    "VALUES (:id, :order_id, 1, 100, :filled_at)"
                ),
                {"id": fill_id, "order_id": order_id, "filled_at": event_time},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash, version) "
                    "VALUES (:id, 1000, 900, 1)"
                ),
                {"id": portfolio_id},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolio_fill_applications(portfolio_id, fill_id, application_sequence, applied_at) "
                    "VALUES (:portfolio_id, :fill_id, 1, :applied_at)"
                ),
                {"portfolio_id": portfolio_id, "fill_id": fill_id, "applied_at": event_time},
            )

            with pytest.raises(IntegrityError, match="PAPER_PORTFOLIO_FILL_APPLICATION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE paper_portfolio_fill_applications SET application_sequence = 2 "
                            "WHERE portfolio_id = :portfolio_id AND fill_id = :fill_id"
                        ),
                        {"portfolio_id": portfolio_id, "fill_id": fill_id},
                    )

            with pytest.raises(IntegrityError, match="PAPER_PORTFOLIO_FILL_APPLICATION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM paper_portfolio_fill_applications "
                            "WHERE portfolio_id = :portfolio_id AND fill_id = :fill_id"
                        ),
                        {"portfolio_id": portfolio_id, "fill_id": fill_id},
                    )

            stored = connection.execute(
                text(
                    "SELECT application_sequence, applied_at FROM paper_portfolio_fill_applications "
                    "WHERE portfolio_id = :portfolio_id AND fill_id = :fill_id"
                ),
                {"portfolio_id": portfolio_id, "fill_id": fill_id},
            ).one()
            assert stored.application_sequence == 1
            assert stored.applied_at == event_time
        finally:
            transaction.rollback()
