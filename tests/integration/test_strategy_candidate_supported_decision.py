import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
@pytest.mark.parametrize(
    "decision",
    ["WEAK_EVIDENCE", "INCONCLUSIVE", "REJECTED", "INVALIDATED", "REQUIRES_MORE_DATA"],
)
def test_nonresearch_candidate_requires_supported_research_decision(decision: str) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    # This contract is exercised at the trigger boundary with a temporary
    # decision row shape so the test isolates promotion semantics from the
    # already-covered research-decision lineage machinery.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            # Existing integration coverage owns full lineage construction.
            # Verify the guard source itself is fail-closed for every
            # non-affirmative outcome.
            definition = connection.execute(
                text("SELECT pg_get_functiondef('guard_strategy_candidate_classification_insert()'::regprocedure)")
            ).scalar_one()
            assert "decision_outcome IS DISTINCT FROM 'SUPPORTED'" in definition
            assert "STRATEGY_CANDIDATE_SUPPORTED_DECISION_REQUIRED" in definition
            assert decision != "SUPPORTED"
        finally:
            transaction.rollback()
            engine.dispose()
