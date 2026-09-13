import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_positions_require_finite_numeric_state() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            portfolio_id = uuid4()
            instrument_id = uuid4()
            connection.execute(
                text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, 'PAPER-POSITION-FINITE', 'TEST', 'ACTIVE')"),
                {"id": instrument_id},
            )
            connection.execute(
                text("INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) VALUES (:id, 1000, 1000)"),
                {"id": portfolio_id},
            )

            invalid_cases = [
                ("quantity", "'NaN'::numeric"),
                ("average_price", "'Infinity'::numeric"),
                ("realized_pnl", "'-Infinity'::numeric"),
                ("total_commission", "'NaN'::numeric"),
            ]
            for column, value_sql in invalid_cases:
                with pytest.raises(IntegrityError):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "INSERT INTO paper_portfolio_positions("
                                "portfolio_id, instrument_id, quantity, average_price, realized_pnl, total_commission"
                                ") VALUES (:portfolio_id, :instrument_id, 1, 100, 0, 0)"
                            ).execution_options(),
                            {"portfolio_id": portfolio_id, "instrument_id": instrument_id},
                        )
                        connection.execute(
                            text(
                                f"UPDATE paper_portfolio_positions SET {column} = {value_sql} "
                                "WHERE portfolio_id = :portfolio_id AND instrument_id = :instrument_id"
                            ),
                            {"portfolio_id": portfolio_id, "instrument_id": instrument_id},
                        )

            connection.execute(
                text(
                    "INSERT INTO paper_portfolio_positions("
                    "portfolio_id, instrument_id, quantity, average_price, realized_pnl, total_commission"
                    ") VALUES (:portfolio_id, :instrument_id, -2, 125, -50, 3)"
                ),
                {"portfolio_id": portfolio_id, "instrument_id": instrument_id},
            )
            row = connection.execute(
                text(
                    "SELECT quantity, average_price, realized_pnl, total_commission "
                    "FROM paper_portfolio_positions "
                    "WHERE portfolio_id = :portfolio_id AND instrument_id = :instrument_id"
                ),
                {"portfolio_id": portfolio_id, "instrument_id": instrument_id},
            ).one()
            assert tuple(row) == (-2, 125, -50, 3)
        finally:
            transaction.rollback()
