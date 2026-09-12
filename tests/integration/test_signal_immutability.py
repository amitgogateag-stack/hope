import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_signal_history_is_append_only() -> None:
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
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id, "symbol": f"SIG-{instrument_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:signal_id, :instrument_id, now(), 'SIGNAL')"
                ),
                {"signal_id": signal_id, "instrument_id": instrument_id},
            )

            with pytest.raises(IntegrityError, match="SIGNAL_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE signals SET state = 'NO_SIGNAL' WHERE signal_id = :signal_id"),
                        {"signal_id": signal_id},
                    )

            with pytest.raises(IntegrityError, match="SIGNAL_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("DELETE FROM signals WHERE signal_id = :signal_id"),
                        {"signal_id": signal_id},
                    )

            stored = connection.execute(
                text("SELECT instrument_id, state FROM signals WHERE signal_id = :signal_id"),
                {"signal_id": signal_id},
            ).one()
            assert stored.instrument_id == instrument_id
            assert stored.state == "SIGNAL"
        finally:
            transaction.rollback()
