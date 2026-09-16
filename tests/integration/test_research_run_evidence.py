import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_research_run_evidence_is_bound_and_immutable() -> None:
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
            experiment_id = f"EXP-EVIDENCE-{uuid4()}"
            config_hash = "d" * 64
            connection.execute(text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"), {"id": strategy_id, "name": str(strategy_id)})
            connection.execute(text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:vid,:sid,'1','commit')"), {"vid": strategy_version_id, "sid": strategy_id})
            connection.execute(text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'GENERIC',TRUE)"), {"id": dataset_id, "name": str(dataset_id)})
            connection.execute(text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:vid,:did,'v1','sealed',TRUE)"), {"vid": dataset_version_id, "did": dataset_id})
            connection.execute(text("INSERT INTO universes(universe_id,name) VALUES (:id,:name)"), {"id": universe_id, "name": str(universe_id)})
            connection.execute(text("INSERT INTO universe_versions(universe_version_id,universe_id,version,pit_certified,declared_member_count) VALUES (:vid,:uid,'v1',TRUE,0)"), {"vid": universe_version_id, "uid": universe_id})
            connection.execute(text("INSERT INTO configuration_snapshots(configuration_hash,canonical_json) VALUES (:hash,'{}'::jsonb)"), {"hash": config_hash})
            connection.execute(text("INSERT INTO experiments(experiment_id,hypothesis,strategy_version_id,dataset_version_id,universe_version_id,configuration_hash,environment,status) VALUES (:eid,'evidence',:sid,:did,:uid,:hash,'BACKTEST','CREATED')"), {"eid": experiment_id, "sid": strategy_version_id, "did": dataset_version_id, "uid": universe_version_id, "hash": config_hash})
            run_id = uuid4()
            connection.execute(text("INSERT INTO research_runs(research_run_id,experiment_id,run_fingerprint,as_of) VALUES (:rid,:eid,:fp,:as_of)"), {"rid": run_id, "eid": experiment_id, "fp": "a" * 64, "as_of": datetime(2026, 1, 2, tzinfo=timezone.utc)})
            connection.execute(text("INSERT INTO research_run_evidence(research_run_id,result_fingerprint,canonical_result) VALUES (:rid,:fp,:result)"), {"rid": run_id, "fp": "b" * 64, "result": '{"net": 1}'})

            with pytest.raises(IntegrityError, match="RESEARCH_RUN_EVIDENCE_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(text("UPDATE research_run_evidence SET result_fingerprint=:fp WHERE research_run_id=:rid"), {"fp": "c" * 64, "rid": run_id})
            with pytest.raises(IntegrityError, match="RESEARCH_RUN_EVIDENCE_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(text("DELETE FROM research_run_evidence WHERE research_run_id=:rid"), {"rid": run_id})
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO research_run_evidence(research_run_id,result_fingerprint,canonical_result) VALUES (:rid,:fp,'{}'::jsonb)"), {"rid": uuid4(), "fp": "d" * 64})
        finally:
            transaction.rollback()
