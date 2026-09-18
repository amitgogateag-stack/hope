import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.domain.research.models import ResearchDecision, ResearchDecisionDefinition
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.research_decisions import (
    SqlAlchemyResearchDecisionRepository,
)


@pytest.mark.integration
def test_research_decisions_require_comparable_evidence_and_are_immutable() -> None:
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
                {"id": strategy_id, "name": f"decision-{strategy_id}"},
            )
            connection.execute(
                text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:vid,:sid,'1','commit')"),
                {"vid": strategy_version_id, "sid": strategy_id},
            )
            connection.execute(
                text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'GENERIC',TRUE)"),
                {"id": dataset_id, "name": f"decision-{dataset_id}"},
            )
            connection.execute(
                text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:vid,:did,'v1','sealed',TRUE)"),
                {"vid": dataset_version_id, "did": dataset_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id,name) VALUES (:id,:name)"),
                {"id": universe_id, "name": f"decision-{universe_id}"},
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

            def insert_experiment(experiment_id: str, config_hash: str) -> None:
                connection.execute(
                    text(
                        "INSERT INTO experiments("
                        "experiment_id,hypothesis,strategy_version_id,dataset_version_id,"
                        "universe_version_id,configuration_hash,environment,status"
                        ") VALUES (:eid,'decision comparison',:sid,:did,:uid,:hash,'BACKTEST','CREATED')"
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
            insert_experiment(control_id, control_hash)
            insert_experiment(variant_id, variant_hash)
            connection.execute(
                text(
                    "INSERT INTO experiment_variants("
                    "variant_experiment_id,control_experiment_id,variant_label"
                    ") VALUES (:variant,:control,'lookback-variant')"
                ),
                {"variant": variant_id, "control": control_id},
            )

            as_of = datetime(2026, 1, 2, tzinfo=timezone.utc)
            control_run_id, variant_run_id = uuid4(), uuid4()
            for run_id, experiment_id, fingerprint in (
                (control_run_id, control_id, "c" * 64),
                (variant_run_id, variant_id, "d" * 64),
            ):
                connection.execute(
                    text(
                        "INSERT INTO research_runs("
                        "research_run_id,experiment_id,run_fingerprint,as_of"
                        ") VALUES (:rid,:eid,:fp,:as_of)"
                    ),
                    {"rid": run_id, "eid": experiment_id, "fp": fingerprint, "as_of": as_of},
                )
                connection.execute(
                    text(
                        "INSERT INTO research_run_evidence("
                        "research_run_id,result_fingerprint,canonical_result"
                        ") VALUES (:rid,:fp,'{}'::jsonb)"
                    ),
                    {"rid": run_id, "fp": fingerprint},
                )

            repository = SqlAlchemyResearchDecisionRepository(connection)
            definition = ResearchDecisionDefinition(
                decision_id=f"DEC-{uuid4()}",
                variant_experiment_id=variant_id,
                control_run_id=control_run_id,
                variant_run_id=variant_run_id,
                decision=ResearchDecision.INCONCLUSIVE,
                rationale="Evidence does not yet support a stronger conclusion",
            )
            repository.create(definition)
            stored = repository.get(definition.decision_id)
            assert stored is not None
            assert stored.decision == ResearchDecision.INCONCLUSIVE
            assert stored.control_run_id == control_run_id
            assert stored.variant_run_id == variant_run_id

            with pytest.raises(IntegrityError, match="RESEARCH_DECISION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE research_decisions SET rationale='changed' WHERE decision_id=:id"),
                        {"id": definition.decision_id},
                    )

            late_variant_run_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO research_runs("
                    "research_run_id,experiment_id,run_fingerprint,as_of"
                    ") VALUES (:rid,:eid,:fp,:as_of)"
                ),
                {
                    "rid": late_variant_run_id,
                    "eid": variant_id,
                    "fp": "e" * 64,
                    "as_of": as_of + timedelta(days=1),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO research_run_evidence("
                    "research_run_id,result_fingerprint,canonical_result"
                    ") VALUES (:rid,:fp,'{}'::jsonb)"
                ),
                {"rid": late_variant_run_id, "fp": "e" * 64},
            )
            with pytest.raises(IntegrityError, match="RESEARCH_DECISION_AS_OF_MISMATCH"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO research_decisions("
                            "decision_id,variant_experiment_id,control_run_id,variant_run_id,decision,rationale"
                            ") VALUES (:id,:variant,:control_run,:variant_run,'INCONCLUSIVE','mismatch')"
                        ),
                        {
                            "id": f"DEC-{uuid4()}",
                            "variant": variant_id,
                            "control_run": control_run_id,
                            "variant_run": late_variant_run_id,
                        },
                    )

            unevidenced_run_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO research_runs("
                    "research_run_id,experiment_id,run_fingerprint,as_of"
                    ") VALUES (:rid,:eid,:fp,:as_of)"
                ),
                {
                    "rid": unevidenced_run_id,
                    "eid": variant_id,
                    "fp": "f" * 64,
                    "as_of": as_of,
                },
            )
            with pytest.raises(IntegrityError, match="RESEARCH_DECISION_VARIANT_EVIDENCE_REQUIRED"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO research_decisions("
                            "decision_id,variant_experiment_id,control_run_id,variant_run_id,decision,rationale"
                            ") VALUES (:id,:variant,:control_run,:variant_run,'REQUIRES_MORE_DATA','missing evidence')"
                        ),
                        {
                            "id": f"DEC-{uuid4()}",
                            "variant": variant_id,
                            "control_run": control_run_id,
                            "variant_run": unevidenced_run_id,
                        },
                    )
        finally:
            transaction.rollback()
