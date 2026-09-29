import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations

UTC = timezone.utc


@pytest.mark.integration
def test_paper_terminal_created_at_must_be_database_authenticated():
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
                {"id": instrument_id, "symbol": f"TERM-TS-{str(instrument_id)[:8]}"},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:id, :instrument_id, :decision_time, 'SIGNAL')"
                ),
                {
                    "id": signal_id,
                    "instrument_id": instrument_id,
                    "decision_time": datetime(2026, 9, 9, 20, 0, tzinfo=UTC),
                },
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
                match="PAPER_TERMINAL_TIMESTAMP_NOT_DATABASE_AUTHENTICATED",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO paper_order_terminal_events("
                            "order_id, outcome, reason_code, event_time, created_at"
                            ") VALUES ("
                            ":order_id, 'REJECTED', 'FORGED_CREATED_AT', :event_time, "
                            "transaction_timestamp() + interval '1 microsecond'"
                            ")"
                        ),
                        {
                            "order_id": order_id,
                            "event_time": datetime(2026, 9, 9, 20, 1, tzinfo=UTC),
                        },
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
