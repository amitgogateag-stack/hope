import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_fill_cannot_precede_signal_decision_time() -> None:
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
            decision_time = datetime(2026, 9, 13, 7, 30, tzinfo=timezone.utc)

            connection.execute(text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, 'PAPER-DECISION-TIME', 'TEST', 'ACTIVE')"), {"id": instrument_id})
            connection.execute(text("INSERT INTO signals(signal_id, instrument_id, decision_time, state) VALUES (:id, :instrument_id, :t, 'SIGNAL')"), {"id": signal_id, "instrument_id": instrument_id, "t": decision_time})
            connection.execute(text("INSERT INTO orders(order_id, signal_id, instrument_id, environment, side, quantity) VALUES (:id, :signal_id, :instrument_id, 'PAPER', 'BUY', 1)"), {"id": order_id, "signal_id": signal_id, "instrument_id": instrument_id})

            with pytest.raises(IntegrityError, match="PAPER_FILL_PRECEDES_DECISION_TIME"):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at, cost_model_version) VALUES (:id, :order_id, 1, 100, 0, 0, :t, 'test-v1')"), {"id": uuid4(), "order_id": order_id, "t": decision_time - timedelta(microseconds=1)})

            valid_fill_id = uuid4()
            connection.execute(text("INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, transaction_cost, filled_at, cost_model_version) VALUES (:id, :order_id, 1, 100, 0, 0, :t, 'test-v1')"), {"id": valid_fill_id, "order_id": order_id, "t": decision_time})
            assert connection.execute(text("SELECT count(*) FROM fills WHERE fill_id=:id"), {"id": valid_fill_id}).scalar_one() == 1
        finally:
            transaction.rollback()
