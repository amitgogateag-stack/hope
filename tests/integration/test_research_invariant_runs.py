import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.validation.research_invariants import (
    build_research_invariant_artifact,
)
from hope.domain.validation.contexts import InvariantContext
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.research_invariant_runs import (
    SqlAlchemyResearchInvariantRunRepository,
)


@pytest.mark.integration
def test_research_invariant_run_is_idempotent_and_immutable() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            strategy_id, strategy_version_id = uuid4(), uuid4()
            dataset_id, dataset_version_id = uuid4(), uuid4()
            universe_id, universe_version_id = uuid4(), uuid4()
            run_id = uuid4()
            experiment_id = f"EXP-INVARIANT-{uuid4()}"

            connection.execute(
                text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"),
                {"id": strategy_id, "name": f"inv-{strategy_id}"},
            )
            connection.execute(
                text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:vid,:sid,'1','commit')"),
                {"vid": strategy_version_id, "sid": strategy_id},
            )
            connection.execute(
                text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'GENERIC',TRUE)"),
                {"id": dataset_id, "name": f"inv-{dataset_id}"},
            )
            connection.execute(
                text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:vid,:did,'v1','sealed',TRUE)"),
                {"vid": dataset_version_id, "did": dataset_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id,name) VALUES (:id,:name)"),
                {"id": universe_id, "name": f"inv-{universe_id}"},
            )
            connection.execute(
                text("INSERT INTO universe_versions(universe_version_id,universe_id,version,pit_certified,declared_member_count) VALUES (:vid,:uid,'v1',TRUE,1)"),
                {"vid": universe_version_id, "uid": universe_id},
            )
            connection.execute(
                text("INSERT INTO configuration_snapshots(configuration_hash,canonical_json) VALUES (:hash,'{}'::jsonb)"),
                {"hash": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO experiments(experiment_id,hypothesis,strategy_version_id,dataset_version_id,"
                    "universe_version_id,configuration_hash,environment,status) "
                    "VALUES (:eid,'invariant evidence',:sid,:did,:uid,:hash,'BACKTEST','CREATED')"
                ),
                {
                    "eid": experiment_id,
                    "sid": strategy_version_id,
                    "did": dataset_version_id,
                    "uid": universe_version_id,
                    "hash": "a" * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO research_runs(research_run_id,experiment_id,run_fingerprint,as_of) "
                    "VALUES (:rid,:eid,:fp,:asof)"
                ),
                {
                    "rid": run_id,
                    "eid": experiment_id,
                    "fp": "b" * 64,
                    "asof": datetime(2026, 9, 19, tzinfo=timezone.utc),
                },
            )

            artifact = build_research_invariant_artifact(
                run_id,
                InvariantContext(
                    declared_member_count=1,
                    evaluated_member_count=1,
                    evaluated_broker_instrument_ids=("B1",),
                ),
            )
            repository = SqlAlchemyResearchInvariantRunRepository(connection)
            assert repository.persist(artifact) is True
            assert repository.persist(artifact) is False
            stored = repository.get(run_id)
            assert stored is not None
            assert stored.result_fingerprint == artifact.result_fingerprint

            with pytest.raises(IntegrityError, match="RESEARCH_INVARIANT_RUN_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE research_invariant_runs SET context_fingerprint=:fp "
                            "WHERE research_run_id=:rid"
                        ),
                        {"fp": "c" * 64, "rid": run_id},
                    )
        finally:
            transaction.rollback()
