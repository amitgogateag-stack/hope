import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleContext
from hope.domain.execution import OrderSide
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from test_paper_portfolio_persistence import persist_fill


@pytest.mark.integration
def test_paper_fill_single_portfolio_ownership_survives_replay() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    first, second, instrument_id = uuid4(), uuid4(), uuid4()
    run = create_scheduled_job_run(
        "paper-single-portfolio-owner",
        datetime(2026, 9, 9, 23, 20, tzinfo=timezone.utc),
    )
    with engine.begin() as connection:
        apply_migrations(connection, Path(__file__).parents[2] / "migrations")
        assert any(
            item["name"] == "uq_paper_portfolio_fill_single_owner"
            and item["column_names"] == ["fill_id"]
            for item in inspect(connection).get_unique_constraints(
                "paper_portfolio_fill_applications"
            )
        )
        connection.execute(
            text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
            ),
            {"id": instrument_id, "symbol": f"FILL-OWNER-{instrument_id.hex[:12]}"},
        )
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        fill = persist_fill(
            connection, PaperCycleContext(run), instrument_id,
            side=OrderSide.BUY, quantity=Decimal("1"),
            price=Decimal("100"), sequence=0, decision_minute=21,
        )
        repository = SqlAlchemyPaperPortfolioRepository(connection)
        assert repository.apply_fill(
            first, Decimal("1000"), fill, job_run_id=run.job_run_id,
        )
        assert not repository.apply_fill(
            first, Decimal("1000"), fill, job_run_id=run.job_run_id,
        )
        with pytest.raises(IntegrityError, match="uq_paper_portfolio_fill_single_owner"):
            repository.apply_fill(
                second, Decimal("1000"), fill, job_run_id=run.job_run_id,
            )
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolio_fill_applications WHERE fill_id=:id"),
            {"id": fill.fill_id},
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": second},
        ).scalar_one() == 0
    engine.dispose()
