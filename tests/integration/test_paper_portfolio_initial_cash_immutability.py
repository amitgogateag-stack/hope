import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_initial_cash_is_immutable() -> None:
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
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) "
                    "VALUES (:portfolio_id, 1000, 1000)"
                ),
                {"portfolio_id": portfolio_id},
            )

            with pytest.raises(IntegrityError, match="PAPER_PORTFOLIO_INITIAL_CASH_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE paper_portfolios SET initial_cash = 2000 "
                            "WHERE portfolio_id = :portfolio_id"
                        ),
                        {"portfolio_id": portfolio_id},
                    )

            connection.execute(
                text(
                    "UPDATE paper_portfolios SET cash = 900, version = 1 "
                    "WHERE portfolio_id = :portfolio_id"
                ),
                {"portfolio_id": portfolio_id},
            )
            row = connection.execute(
                text(
                    "SELECT initial_cash, cash, version FROM paper_portfolios "
                    "WHERE portfolio_id = :portfolio_id"
                ),
                {"portfolio_id": portfolio_id},
            ).one()
            assert tuple(row) == (1000, 900, 1)
        finally:
            transaction.rollback()
