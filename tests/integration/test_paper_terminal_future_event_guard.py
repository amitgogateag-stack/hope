import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_terminal_event_time_cannot_be_in_future():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    instrument_id = uuid4()
    signal_id = uuid4()
    order_id = uuid4()

    try:
        with engine.begin() as connection:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id, "symbol": f"TERM-FUT-{str(instrument_id)[:8]}"},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:id, :instrument_id, transaction_timestamp(), 'SIGNAL')"
                ),
                {"id": signal_id, "instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) "
                    "VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"
                ),
                {
                    "id": order_id,
                    "signal_id": signal_id,
                    "instrument_id": instrument_id,
                },
            )

            with pytest.raises(
                IntegrityError,
                match="PAPER_TERMINAL_EVENT_TIME_IN_FUTURE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO paper_order_terminal_events("
                            "order_id, outcome, reason_code, event_time"
                            ") VALUES ("
                            ":order_id, 'REJECTED', 'FUTURE_EVENT_PROBE', "
                            "transaction_timestamp() + interval '1 day'"
                            ")"
                        ),
                        {"order_id": order_id},
                    )

            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_order_terminal_events "
                    "WHERE order_id=:order_id"
                ),
                {"order_id": order_id},
            ).scalar_one() == 0
    finally:
        engine.dispose()
