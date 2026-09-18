import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.experiments.evaluation_protocol import (
    REQUIRED_EVALUATION_STAGES,
    ResearchEvaluationPlanDefinition,
)
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.research_evaluation_plans import (
    SqlAlchemyResearchEvaluationPlanRepository,
)


def protocol() -> dict[str, dict[str, object]]:
    return {stage: {"enabled": True} for stage in REQUIRED_EVALUATION_STAGES}


@pytest.mark.integration
def test_evaluation_plan_is_predeclared_before_runs_and_immutable() -> None:
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

            connection.execute(
                text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"),
                {"id": strategy_id, "name": f"plan-{strategy_id}"},
            )
            connection.execute(
                text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:vid,:sid,'1','commit')"),
                {"vid": strategy_version_id, "sid": strategy_id},
            )
            connection.execute(
                text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'GENERIC',TRUE)"),
                {"id": dataset_id, "name": f"plan-{dataset_id}"},
            )
            connection.execute(
                text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:vid,:did,'v1','sealed',TRUE)"),
                {"vid": dataset_version_id, "did": dataset_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id,name) VALUES (:id,:name)"),
                {"id": universe_id, "name": f"plan-{universe_id}"},
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
                        ") VALUES (:eid,'evaluation plan',:sid,:did,:uid,:hash,'BACKTEST','CREATED')"
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
                    ") VALUES (:variant,:control,'evaluation-plan')"
                ),
                {"variant": variant_id, "control": control_id},
            )

            repository = SqlAlchemyResearchEvaluationPlanRepository(connection)
            definition = ResearchEvaluationPlanDefinition(
                variant_experiment_id=variant_id,
                protocol=protocol(),
            )
            assert repository.persist(definition) is True
            assert repository.persist(definition) is False
            stored = repository.get(variant_id)
            assert stored is not None
            assert stored.canonical_protocol == definition.protocol

            with pytest.raises(IntegrityError, match="RESEARCH_EVALUATION_PLAN_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE research_evaluation_plans SET protocol_hash=:hash "
                            "WHERE variant_experiment_id=:variant"
                        ),
                        {"hash": "c" * 64, "variant": variant_id},
                    )

            late_control_id = f"EXP-CONTROL-{uuid4()}"
            late_variant_id = f"EXP-VARIANT-{uuid4()}"
            add_experiment(late_control_id, control_hash)
            add_experiment(late_variant_id, variant_hash)
            connection.execute(
                text(
                    "INSERT INTO experiment_variants("
                    "variant_experiment_id,control_experiment_id,variant_label"
                    ") VALUES (:variant,:control,'late-plan')"
                ),
                {"variant": late_variant_id, "control": late_control_id},
            )
            connection.execute(
                text(
                    "INSERT INTO research_runs("
                    "research_run_id,experiment_id,run_fingerprint,as_of"
                    ") VALUES (:rid,:eid,:fp,now())"
                ),
                {"rid": uuid4(), "eid": late_control_id, "fp": "d" * 64},
            )
            with pytest.raises(
                IntegrityError,
                match="RESEARCH_EVALUATION_PLAN_MUST_PRECEDE_RUNS",
            ):
                with connection.begin_nested():
                    repository.persist(
                        ResearchEvaluationPlanDefinition(
                            variant_experiment_id=late_variant_id,
                            protocol=protocol(),
                        )
                    )
        finally:
            transaction.rollback()
