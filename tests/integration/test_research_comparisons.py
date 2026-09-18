import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.experiments.comparisons import research_comparison_fingerprint
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.research_comparisons import (
    ResearchComparisonRecord,
    SqlAlchemyResearchComparisonRepository,
)


@pytest.mark.integration
def test_research_comparison_is_bound_to_exact_evidence_and_immutable() -> None:
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
            control_hash, variant_hash = "a" * 64, "b" * 64
            control_result_fp, variant_result_fp = "c" * 64, "d" * 64

            connection.execute(
                text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"),
                {"id": strategy_id, "name": f"comparison-{strategy_id}"},
            )
            connection.execute(
                text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:vid,:sid,'1','commit')"),
                {"vid": strategy_version_id, "sid": strategy_id},
            )
            connection.execute(
                text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'GENERIC',TRUE)"),
                {"id": dataset_id, "name": f"comparison-{dataset_id}"},
            )
            connection.execute(
                text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:vid,:did,'v1','sealed',TRUE)"),
                {"vid": dataset_version_id, "did": dataset_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id,name) VALUES (:id,:name)"),
                {"id": universe_id, "name": f"comparison-{universe_id}"},
            )
            connection.execute(
                text("INSERT INTO universe_versions(universe_version_id,universe_id,version,pit_certified,declared_member_count) VALUES (:vid,:uid,'v1',TRUE,0)"),
                {"vid": universe_version_id, "uid": universe_id},
            )
            for config_hash in (control_hash, variant_hash):
                connection.execute(
                    text("INSERT INTO configuration_snapshots(configuration_hash,canonical_json) VALUES (:hash,'{}'::jsonb)"),
                    {"hash": config_hash},
                )

            def add_experiment(experiment_id: str, config_hash: str) -> None:
                connection.execute(
                    text(
                        "INSERT INTO experiments("
                        "experiment_id,hypothesis,strategy_version_id,dataset_version_id,"
                        "universe_version_id,configuration_hash,environment,status"
                        ") VALUES (:eid,'comparison',:sid,:did,:uid,:hash,'BACKTEST','CREATED')"
                    ),
                    {
                        "eid": experiment_id,
                        "sid": strategy_version_id,
                        "did": dataset_version_id,
                        "uid": universe_version_id,
                        "hash": config_hash,
                    },
                )

            control_id = f"EXP-CONTROL-{uuid4()}"
            variant_id = f"EXP-VARIANT-{uuid4()}"
            add_experiment(control_id, control_hash)
            add_experiment(variant_id, variant_hash)
            connection.execute(
                text(
                    "INSERT INTO experiment_variants("
                    "variant_experiment_id,control_experiment_id,variant_label"
                    ") VALUES (:variant,:control,'comparison')"
                ),
                {"variant": variant_id, "control": control_id},
            )

            as_of = datetime(2026, 1, 2, tzinfo=timezone.utc)
            control_run_id, variant_run_id = uuid4(), uuid4()
            for run_id, experiment_id, run_fp, result_fp in (
                (control_run_id, control_id, "1" * 64, control_result_fp),
                (variant_run_id, variant_id, "2" * 64, variant_result_fp),
            ):
                connection.execute(
                    text(
                        "INSERT INTO research_runs(research_run_id,experiment_id,run_fingerprint,as_of) "
                        "VALUES (:rid,:eid,:fp,:as_of)"
                    ),
                    {"rid": run_id, "eid": experiment_id, "fp": run_fp, "as_of": as_of},
                )
                connection.execute(
                    text(
                        "INSERT INTO research_run_evidence("
                        "research_run_id,result_fingerprint,canonical_result"
                        ") VALUES (:rid,:fp,'{}'::jsonb)"
                    ),
                    {"rid": run_id, "fp": result_fp},
                )

            canonical = {
                "schema": "hope.research-comparison.v1",
                "control": {"result_fingerprint": control_result_fp},
                "variant": {"result_fingerprint": variant_result_fp},
                "deltas": {"total_return": "0.01"},
            }
            comparison_fp = research_comparison_fingerprint(canonical)
            repository = SqlAlchemyResearchComparisonRepository(connection)
            comparison_id = repository.deterministic_id(
                variant_experiment_id=variant_id,
                control_run_id=control_run_id,
                variant_run_id=variant_run_id,
                control_result_fingerprint=control_result_fp,
                variant_result_fingerprint=variant_result_fp,
                comparison_fingerprint=comparison_fp,
            )
            comparison = ResearchComparisonRecord(
                comparison_id=comparison_id,
                variant_experiment_id=variant_id,
                control_run_id=control_run_id,
                variant_run_id=variant_run_id,
                control_result_fingerprint=control_result_fp,
                variant_result_fingerprint=variant_result_fp,
                comparison_fingerprint=comparison_fp,
                canonical_comparison=canonical,
            )
            assert repository.persist(comparison) is True
            assert repository.persist(comparison) is False

            stored = repository.get_by_run_pair(
                variant_id, control_run_id, variant_run_id
            )
            assert stored is not None
            assert stored.comparison_fingerprint == comparison_fp

            with pytest.raises(IntegrityError, match="RESEARCH_COMPARISON_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE research_comparisons SET comparison_fingerprint=:fp "
                            "WHERE comparison_id=:id"
                        ),
                        {"fp": "e" * 64, "id": comparison_id},
                    )

            with pytest.raises(IntegrityError, match="RESEARCH_COMPARISON_CONTROL_FINGERPRINT_MISMATCH"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO research_comparisons("
                            "comparison_id,variant_experiment_id,control_run_id,variant_run_id,"
                            "control_result_fingerprint,variant_result_fingerprint,"
                            "comparison_fingerprint,canonical_comparison"
                            ") VALUES (:id,:variant,:control_run,:variant_run,:control_fp,"
                            ":variant_fp,:comparison_fp,'{}'::jsonb)"
                        ),
                        {
                            "id": uuid4(),
                            "variant": variant_id,
                            "control_run": control_run_id,
                            "variant_run": variant_run_id,
                            "control_fp": "f" * 64,
                            "variant_fp": variant_result_fp,
                            "comparison_fp": "a" * 64,
                        },
                    )
        finally:
            transaction.rollback()
