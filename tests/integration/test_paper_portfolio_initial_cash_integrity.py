import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_initial_cash_must_be_finite_and_nonnegative() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)

            for invalid_cash in ("-0.01", "NaN", "Infinity"):
                with pytest.raises(IntegrityError):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) "
                                "VALUES (:portfolio_id, CAST(:initial_cash AS NUMERIC), 0)"
                            ),
                            {"portfolio_id": uuid4(), "initial_cash": invalid_cash},
                        )

            portfolio_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) "
                    "VALUES (:portfolio_id, 0, 0)"
                ),
                {"portfolio_id": portfolio_id},
            )
            stored = connection.execute(
                text("SELECT initial_cash FROM paper_portfolios WHERE portfolio_id=:portfolio_id"),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
            assert stored == 0
        finally:
            transaction.rollback()
