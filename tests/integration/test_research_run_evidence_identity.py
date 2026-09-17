import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_certified_research_evidence_is_bound_to_parent_run_identity() -> None:
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
            experiment_id = f"EXP-EVIDENCE-ID-{uuid4()}"
            config_hash = "d" * 64
            run_fingerprint = "a" * 64
            run_id = uuid4()

            connection.execute(text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"), {"id": strategy_id, "name": str(strategy_id)})
            connection.execute(text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:vid,:sid,'1','commit')"), {"vid": strategy_version_id, "sid": strategy_id})
            connection.execute(text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'GENERIC',TRUE)"), {"id": dataset_id, "name": str(dataset_id)})
            connection.execute(text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:vid,:did,'v1','sealed',TRUE)"), {"vid": dataset_version_id, "did": dataset_id})
            connection.execute(text("INSERT INTO universes(universe_id,name) VALUES (:id,:name)"), {"id": universe_id, "name": str(universe_id)})
            connection.execute(text("INSERT INTO universe_versions(universe_version_id,universe_id,version,pit_certified,declared_member_count) VALUES (:vid,:uid,'v1',TRUE,0)"), {"vid": universe_version_id, "uid": universe_id})
            connection.execute(text("INSERT INTO configuration_snapshots(configuration_hash,canonical_json) VALUES (:hash,'{}'::jsonb)"), {"hash": config_hash})
            connection.execute(text("INSERT INTO experiments(experiment_id,hypothesis,strategy_version_id,dataset_version_id,universe_version_id,configuration_hash,environment,status) VALUES (:eid,'evidence identity',:sid,:did,:uid,:hash,'BACKTEST','CREATED')"), {"eid": experiment_id, "sid": strategy_version_id, "did": dataset_version_id, "uid": universe_version_id, "hash": config_hash})
            connection.execute(text("INSERT INTO research_runs(research_run_id,experiment_id,run_fingerprint,as_of) VALUES (:rid,:eid,:fp,:as_of)"), {"rid": run_id, "eid": experiment_id, "fp": run_fingerprint, "as_of": datetime(2026, 1, 2, tzinfo=timezone.utc)})

            def evidence(*, embedded_experiment_id=experiment_id, embedded_run_fingerprint=run_fingerprint):
                return json.dumps({
                    "schema": "hope.certified-backtest-result.v2",
                    "execution_provenance": {},
                    "research_provenance": {
                        "experiment_id": embedded_experiment_id,
                        "run_fingerprint": embedded_run_fingerprint,
                    },
                    "backtest": {},
                })

            valid_id = uuid4()
            connection.execute(text("INSERT INTO research_runs(research_run_id,experiment_id,run_fingerprint,as_of) VALUES (:rid,:eid,:fp,:as_of)"), {"rid": valid_id, "eid": experiment_id, "fp": "b" * 64, "as_of": datetime(2026, 1, 3, tzinfo=timezone.utc)})
            connection.execute(text("INSERT INTO research_run_evidence(research_run_id,result_fingerprint,canonical_result) VALUES (:rid,:fp,CAST(:result AS JSONB))"), {"rid": valid_id, "fp": "1" * 64, "result": evidence(embedded_run_fingerprint="b" * 64)})

            with pytest.raises(IntegrityError, match="RESEARCH_RUN_CERTIFIED_EVIDENCE_RUN_FINGERPRINT_MISMATCH"):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO research_run_evidence(research_run_id,result_fingerprint,canonical_result) VALUES (:rid,:fp,CAST(:result AS JSONB))"), {"rid": run_id, "fp": "2" * 64, "result": evidence(embedded_run_fingerprint="f" * 64)})

            with pytest.raises(IntegrityError, match="RESEARCH_RUN_CERTIFIED_EVIDENCE_EXPERIMENT_MISMATCH"):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO research_run_evidence(research_run_id,result_fingerprint,canonical_result) VALUES (:rid,:fp,CAST(:result AS JSONB))"), {"rid": run_id, "fp": "3" * 64, "result": evidence(embedded_experiment_id="EXP-WRONG")})

            with pytest.raises(IntegrityError, match="RESEARCH_RUN_CERTIFIED_EVIDENCE_STRUCTURE_INVALID"):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO research_run_evidence(research_run_id,result_fingerprint,canonical_result) VALUES (:rid,:fp,CAST(:result AS JSONB))"), {"rid": run_id, "fp": "4" * 64, "result": json.dumps({"schema": "hope.certified-backtest-result.v2", "research_provenance": {"experiment_id": experiment_id, "run_fingerprint": run_fingerprint}})})
        finally:
            transaction.rollback()
