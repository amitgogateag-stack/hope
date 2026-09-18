import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.domain.research.models import ExperimentVariantDefinition
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.experiment_variants import (
    SqlAlchemyExperimentVariantRepository,
)


@pytest.mark.integration
def test_experiment_variants_are_predeclared_comparable_and_immutable() -> None:
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
            other_dataset_id, other_dataset_version_id = uuid4(), uuid4()
            universe_id, universe_version_id = uuid4(), uuid4()
            control_hash, variant_hash = "a" * 64, "b" * 64

            connection.execute(
                text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"),
                {"id": strategy_id, "name": f"variant-{strategy_id}"},
            )
            connection.execute(
                text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:vid,:sid,'1','commit')"),
                {"vid": strategy_version_id, "sid": strategy_id},
            )
            connection.execute(
                text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'GENERIC',TRUE)"),
                {"id": dataset_id, "name": f"variant-{dataset_id}"},
            )
            connection.execute(
                text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:vid,:did,'v1','sealed',TRUE)"),
                {"vid": dataset_version_id, "did": dataset_id},
            )
            connection.execute(
                text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'GENERIC',TRUE)"),
                {"id": other_dataset_id, "name": f"variant-{other_dataset_id}"},
            )
            connection.execute(
                text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:vid,:did,'v1','sealed',TRUE)"),
                {"vid": other_dataset_version_id, "did": other_dataset_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id,name) VALUES (:id,:name)"),
                {"id": universe_id, "name": f"variant-{universe_id}"},
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

            def insert_experiment(experiment_id, *, config_hash=control_hash, dataset=dataset_version_id):
                connection.execute(
                    text(
                        "INSERT INTO experiments("
                        "experiment_id,hypothesis,strategy_version_id,dataset_version_id,"
                        "universe_version_id,configuration_hash,environment,status"
                        ") VALUES (:eid,'variant comparison',:sid,:did,:uid,:hash,'BACKTEST','CREATED')"
                    ),
                    {
                        "eid": experiment_id,
                        "sid": strategy_version_id,
                        "did": dataset,
                        "uid": universe_version_id,
                        "hash": config_hash,
                    },
                )

            control_id = f"EXP-CONTROL-{uuid4()}"
            variant_id = f"EXP-VARIANT-{uuid4()}"
            insert_experiment(control_id)
            insert_experiment(variant_id, config_hash=variant_hash)

            repository = SqlAlchemyExperimentVariantRepository(connection)
            repository.create(
                ExperimentVariantDefinition(
                    control_experiment_id=control_id,
                    variant_experiment_id=variant_id,
                    variant_label="lookback-variant",
                )
            )
            stored = repository.get(variant_id)
            assert stored is not None
            assert stored.control_experiment_id == control_id
            assert stored.variant_experiment_id == variant_id
            assert stored.variant_label == "lookback-variant"
            assert stored.predeclared_at.tzinfo is not None

            with pytest.raises(IntegrityError, match="EXPERIMENT_VARIANT_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE experiment_variants SET variant_label='changed' WHERE variant_experiment_id=:eid"),
                        {"eid": variant_id},
                    )

            late_control = f"EXP-LATE-CONTROL-{uuid4()}"
            late_variant = f"EXP-LATE-VARIANT-{uuid4()}"
            insert_experiment(late_control)
            insert_experiment(late_variant, config_hash=variant_hash)
            connection.execute(
                text("INSERT INTO research_runs(research_run_id,experiment_id,run_fingerprint,as_of) VALUES (:rid,:eid,:fp,:as_of)"),
                {
                    "rid": uuid4(),
                    "eid": late_control,
                    "fp": "c" * 64,
                    "as_of": datetime(2026, 1, 2, tzinfo=timezone.utc),
                },
            )
            with pytest.raises(IntegrityError, match="EXPERIMENT_VARIANT_MUST_BE_PREDECLARED_BEFORE_RUNS"):
                with connection.begin_nested():
                    connection.execute(
                        text("INSERT INTO experiment_variants(variant_experiment_id,control_experiment_id,variant_label) VALUES (:variant,:control,'late')"),
                        {"variant": late_variant, "control": late_control},
                    )

            same_control = f"EXP-SAME-CONTROL-{uuid4()}"
            same_variant = f"EXP-SAME-VARIANT-{uuid4()}"
            insert_experiment(same_control)
            insert_experiment(same_variant)
            with pytest.raises(IntegrityError, match="EXPERIMENT_VARIANT_REQUIRES_MATERIAL_CHANGE"):
                with connection.begin_nested():
                    connection.execute(
                        text("INSERT INTO experiment_variants(variant_experiment_id,control_experiment_id,variant_label) VALUES (:variant,:control,'same')"),
                        {"variant": same_variant, "control": same_control},
                    )

            mismatch_control = f"EXP-DATA-CONTROL-{uuid4()}"
            mismatch_variant = f"EXP-DATA-VARIANT-{uuid4()}"
            insert_experiment(mismatch_control)
            insert_experiment(
                mismatch_variant,
                config_hash=variant_hash,
                dataset=other_dataset_version_id,
            )
            with pytest.raises(IntegrityError, match="EXPERIMENT_VARIANT_DATASET_MISMATCH"):
                with connection.begin_nested():
                    connection.execute(
                        text("INSERT INTO experiment_variants(variant_experiment_id,control_experiment_id,variant_label) VALUES (:variant,:control,'dataset-mismatch')"),
                        {"variant": mismatch_variant, "control": mismatch_control},
                    )
        finally:
            transaction.rollback()
