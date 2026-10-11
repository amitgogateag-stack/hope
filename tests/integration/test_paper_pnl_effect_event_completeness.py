"""Fail-closed transaction boundary for PNL effects without durable accounting."""
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


@pytest.mark.integration
def test_orphan_pnl_effect_cannot_commit_and_rolls_back() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url)
    event_id = uuid4()
    run = create_scheduled_job_run(
        f"pnl-orphan-commit-guard:{uuid4()}",
        datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    try:
        with engine.begin() as connection:
            apply_migrations(connection, Path(__file__).parents[2] / "migrations")
            assert SqlAlchemyJobRunRepository(connection).claim(run) is True

        # A canonical, claimed-job PNL effect previously committed with no
        # portfolio event, leaving a false-positive durable accounting receipt.
        with pytest.raises(
            IntegrityError,
            match="PAPER_PNL_EFFECT_WITHOUT_PORTFOLIO_EVENT",
        ):
            with engine.begin() as connection:
                effect = create_paper_effect(
                    run, PaperEffectType.PNL, event_id, "a" * 64
                )
                assert SqlAlchemyPaperEffectRepository(connection).record(effect) is True

        with engine.begin() as connection:
            assert connection.execute(
                text(
                    "SELECT count(*) FROM paper_effects "
                    "WHERE effect_type='PNL' AND entity_id=:event_id"
                ),
                {"event_id": event_id},
            ).scalar_one() == 0
    finally:
        engine.dispose()
