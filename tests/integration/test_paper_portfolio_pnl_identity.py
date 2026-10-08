import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.paper.portfolio_pnl import paper_portfolio_pnl_event_id
from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_database_uuid_v5_matches_paper_pnl_identity_contract() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)
        for portfolio_id, fill_id in ((uuid4(), uuid4()), (uuid4(), uuid4())):
            name = f"hope:paper:portfolio-pnl:{portfolio_id}:{fill_id}"
            database_id = connection.execute(
                text("SELECT hope_uuid_v5(:namespace_id, :name)"),
                {"namespace_id": NAMESPACE_URL, "name": name},
            ).scalar_one()
            assert database_id == uuid5(NAMESPACE_URL, name)
            assert database_id == paper_portfolio_pnl_event_id(portfolio_id, fill_id)


@pytest.mark.integration
def test_paper_portfolio_pnl_rejects_noncanonical_identity() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    event_time = datetime(2026, 10, 8, 7, 0, tzinfo=timezone.utc)
    instrument_id = uuid4()
    signal_id = uuid4()
    order_id = uuid4()
    fill_id = uuid4()
    portfolio_id = uuid4()
    canonical_id = paper_portfolio_pnl_event_id(portfolio_id, fill_id)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:id, 'PNL-IDENTITY', 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO signals(signal_id, instrument_id, decision_time, state) "
                    "VALUES (:id, :instrument_id, :event_time, 'SIGNAL')"
                ),
                {"id": signal_id, "instrument_id": instrument_id, "event_time": event_time},
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
                    "INSERT INTO fills(fill_id, order_id, quantity, fill_price, slippage, "
                    "transaction_cost, filled_at, cost_model_version) "
                    "VALUES (:id, :order_id, 1, 100, 0, 0.25, :event_time, 'test-cost-v1')"
                ),
                {"id": fill_id, "order_id": order_id, "event_time": event_time},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) "
                    "VALUES (:id, 1000, 900)"
                ),
                {"id": portfolio_id},
            )
            connection.execute(
                text(
                    "INSERT INTO paper_portfolio_fill_applications("
                    "portfolio_id, fill_id, application_sequence, applied_at"
                    ") VALUES (:portfolio_id, :fill_id, 1, :event_time)"
                ),
                {"portfolio_id": portfolio_id, "fill_id": fill_id, "event_time": event_time},
            )
            insert = text(
                "INSERT INTO paper_portfolio_pnl_events("
                "pnl_event_id, portfolio_id, fill_id, instrument_id, "
                "realized_pnl_delta, commission_delta, event_time"
                ") VALUES ("
                ":event_id, :portfolio_id, :fill_id, :instrument_id, 0, 0.25, :event_time"
                ")"
            )
            params = {
                "portfolio_id": portfolio_id,
                "fill_id": fill_id,
                "instrument_id": instrument_id,
                "event_time": event_time,
            }

            with pytest.raises(
                IntegrityError,
                match="PAPER_PORTFOLIO_PNL_IDENTITY_MISMATCH",
            ):
                with connection.begin_nested():
                    connection.execute(insert, {**params, "event_id": uuid4()})

            connection.execute(insert, {**params, "event_id": canonical_id})
            assert connection.execute(
                text(
                    "SELECT pnl_event_id FROM paper_portfolio_pnl_events "
                    "WHERE portfolio_id=:portfolio_id AND fill_id=:fill_id"
                ),
                {"portfolio_id": portfolio_id, "fill_id": fill_id},
            ).scalar_one() == canonical_id
        finally:
            transaction.rollback()
