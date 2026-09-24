import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_strategy_candidate_transition_holds_transaction_scoped_capacity_lock() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as writer, engine.connect() as probe:
        transaction = writer.begin()
        try:
            strategy_id = uuid4()
            strategy_version_id = uuid4()
            writer.execute(
                text(
                    "INSERT INTO strategies(strategy_id,name,family) "
                    "VALUES (:sid,:name,'TEST_FAMILY')"
                ),
                {"sid": strategy_id, "name": f"capacity-lock-{strategy_id}"},
            )
            writer.execute(
                text(
                    "INSERT INTO strategy_versions("
                    "strategy_version_id,strategy_id,version,code_commit"
                    ") VALUES (:vid,:sid,'v1','capacity-lock-commit')"
                ),
                {"vid": strategy_version_id, "sid": strategy_id},
            )
            writer.execute(
                text(
                    "INSERT INTO strategy_candidate_classifications("
                    "classification_id,strategy_version_id,markets,state,rationale"
                    ") VALUES (:cid,:vid,ARRAY['USA'],'RESEARCH','Acquire capacity lock')"
                ),
                {"cid": uuid4(), "vid": strategy_version_id},
            )

            assert probe.execute(
                text(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtext('hope:strategy-candidate-capacity')::bigint)"
                )
            ).scalar_one() is False
        finally:
            transaction.rollback()

        assert probe.execute(
            text(
                "SELECT pg_try_advisory_xact_lock("
                "hashtext('hope:strategy-candidate-capacity')::bigint)"
            )
        ).scalar_one() is True
