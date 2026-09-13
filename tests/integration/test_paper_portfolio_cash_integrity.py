import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_paper_portfolio_cash_must_be_finite_but_may_be_negative() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)

            for invalid_cash in ("NaN", "Infinity", "-Infinity"):
                with pytest.raises(IntegrityError):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) "
                                "VALUES (:portfolio_id, 100, CAST(:cash AS NUMERIC))"
                            ),
                            {"portfolio_id": uuid4(), "cash": invalid_cash},
                        )

            portfolio_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO paper_portfolios(portfolio_id, initial_cash, cash) "
                    "VALUES (:portfolio_id, 100, -1.25)"
                ),
                {"portfolio_id": portfolio_id},
            )
            stored = connection.execute(
                text("SELECT cash FROM paper_portfolios WHERE portfolio_id=:portfolio_id"),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
            assert stored == -1.25
        finally:
            transaction.rollback()
