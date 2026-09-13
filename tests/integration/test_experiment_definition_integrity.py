import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_experiment_definition_rejects_invalid_immutable_fields() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            dataset_id = uuid4()
            dataset_version_id = uuid4()
            universe_id = uuid4()
            universe_version_id = uuid4()
            strategy_id = uuid4()
            strategy_version_id = uuid4()
            config_hash = "a" * 64

            connection.execute(text("INSERT INTO datasets(dataset_id, name, source) VALUES (:id, 'exp-def', 'TEST')"), {"id": dataset_id})
            connection.execute(text("INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label, immutable) VALUES (:id, :dataset_id, 'v1', 'TEST', TRUE)"), {"id": dataset_version_id, "dataset_id": dataset_id})
            connection.execute(text("INSERT INTO universes(universe_id, name) VALUES (:id, 'exp-def')"), {"id": universe_id})
            connection.execute(text("INSERT INTO universe_versions(universe_version_id, universe_id, version, declared_member_count) VALUES (:id, :universe_id, 'v1', 0)"), {"id": universe_version_id, "universe_id": universe_id})
            connection.execute(text("INSERT INTO strategies(strategy_id, name, family) VALUES (:id, 'exp-def', 'TEST')"), {"id": strategy_id})
            connection.execute(text("INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) VALUES (:id, :strategy_id, 'v1', 'deadbeef')"), {"id": strategy_version_id, "strategy_id": strategy_id})
            connection.execute(text("INSERT INTO configuration_snapshots(configuration_hash, canonical_json) VALUES (:hash, '{}'::jsonb)"), {"hash": config_hash})

            base = {
                "strategy_version_id": strategy_version_id,
                "dataset_version_id": dataset_version_id,
                "universe_version_id": universe_version_id,
                "configuration_hash": config_hash,
            }

            invalid_rows = [
                ("   ", "hypothesis", "CREATED", "ck_experiments_id_nonblank"),
                ("exp-blank-hypothesis", "   ", "CREATED", "ck_experiments_hypothesis_nonblank"),
                ("exp-bad-status", "hypothesis", "RUNNING", "ck_experiments_status_created"),
            ]
            for experiment_id, hypothesis, status, constraint in invalid_rows:
                with pytest.raises(IntegrityError, match=constraint):
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                "INSERT INTO experiments(experiment_id, hypothesis, strategy_version_id, dataset_version_id, universe_version_id, configuration_hash, environment, status) "
                                "VALUES (:experiment_id, :hypothesis, :strategy_version_id, :dataset_version_id, :universe_version_id, :configuration_hash, 'RESEARCH', :status)"
                            ),
                            {"experiment_id": experiment_id, "hypothesis": hypothesis, "status": status, **base},
                        )

            connection.execute(
                text(
                    "INSERT INTO experiments(experiment_id, hypothesis, strategy_version_id, dataset_version_id, universe_version_id, configuration_hash, environment, status) "
                    "VALUES ('exp-valid', 'hypothesis', :strategy_version_id, :dataset_version_id, :universe_version_id, :configuration_hash, 'RESEARCH', 'CREATED')"
                ),
                base,
            )
        finally:
            transaction.rollback()
