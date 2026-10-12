"""Reject privileged in-flight PAPER recovery after its job loses CLAIMED status."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository


@pytest.mark.integration
def test_inflight_recovery_requires_durable_claim_even_for_empty_portfolio() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    try:
        with engine.begin() as connection:
            apply_migrations(connection, migrations_dir)
            run = create_scheduled_job_run(
                f"paper-inflight-recovery-{uuid4()}",
                datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc),
            )
            jobs = SqlAlchemyJobRunRepository(connection)
            portfolio = SqlAlchemyPaperPortfolioRepository(connection)
            portfolio_id = uuid4()

            # A non-existent job ID must not bypass the authenticated-job path.
            with pytest.raises(
                RuntimeError, match="PAPER_PORTFOLIO_INFLIGHT_JOB_NOT_CLAIMED",
            ):
                portfolio.verify_accounting_history(
                    portfolio_id, current_job_run_id=uuid4(),
                )

            assert jobs.claim(run) is True
            assert portfolio.verify_accounting_history(
                portfolio_id, current_job_run_id=run.job_run_id,
            ) is None

            assert jobs.complete(create_job_run_completion(
                run,
                JobRunStatus.FAILED,
                run.scheduled_for + timedelta(minutes=5),
                failure_code="EXPECTED_INFLIGHT_RECOVERY_GUARD",
            )) is True
            with pytest.raises(
                RuntimeError, match="PAPER_PORTFOLIO_INFLIGHT_JOB_NOT_CLAIMED",
            ):
                portfolio.verify_accounting_history(
                    portfolio_id, current_job_run_id=run.job_run_id,
                )
    finally:
        engine.dispose()
