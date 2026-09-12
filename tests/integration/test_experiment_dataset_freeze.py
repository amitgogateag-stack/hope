import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_experiment_requires_sealed_dataset_version() -> None:
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
            dataset_id = uuid4()
            dataset_version_id = uuid4()
            universe_id = uuid4()
            universe_version_id = uuid4()
            strategy_id = uuid4()
            strategy_version_id = uuid4()
            config_hash = "a" * 64

            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                    "VALUES (:dataset_id, :name, 'TEST', TRUE)"
                ),
                {"dataset_id": dataset_id, "name": f"experiment-freeze-{dataset_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions("
                    "dataset_version_id, dataset_id, version, vintage_label, immutable"
                    ") VALUES (:version_id, :dataset_id, 'v1', 'staging', FALSE)"
                ),
                {"version_id": dataset_version_id, "dataset_id": dataset_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:universe_id, :name)"),
                {"universe_id": universe_id, "name": f"freeze-universe-{universe_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_versions("
                    "universe_version_id, universe_id, version, pit_certified, declared_member_count"
                    ") VALUES (:version_id, :universe_id, 'v1', TRUE, 0)"
                ),
                {"version_id": universe_version_id, "universe_id": universe_id},
            )
            connection.execute(
                text(
                    "INSERT INTO strategies(strategy_id, name, family) "
                    "VALUES (:strategy_id, :name, 'TEST')"
                ),
                {"strategy_id": strategy_id, "name": f"freeze-strategy-{strategy_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                    "VALUES (:version_id, :strategy_id, 'v1', 'test-commit')"
                ),
                {"version_id": strategy_version_id, "strategy_id": strategy_id},
            )
            connection.execute(
                text(
                    "INSERT INTO configuration_snapshots(configuration_hash, canonical_json) "
                    "VALUES (:hash, CAST(:json AS JSONB))"
                ),
                {"hash": config_hash, "json": "{}"},
            )

            insert_experiment = text(
                "INSERT INTO experiments("
                "experiment_id, hypothesis, strategy_version_id, dataset_version_id, "
                "universe_version_id, configuration_hash, environment, status"
                ") VALUES ("
                ":experiment_id, 'freeze dataset provenance', :strategy_version_id, :dataset_version_id, "
                ":universe_version_id, :configuration_hash, 'RESEARCH', 'CREATED'"
                ")"
            )

            with pytest.raises(IntegrityError, match="EXPERIMENT_DATASET_VERSION_NOT_FROZEN"):
                with connection.begin_nested():
                    connection.execute(
                        insert_experiment,
                        {
                            "experiment_id": "EXP-STAGING-DATASET",
                            "strategy_version_id": strategy_version_id,
                            "dataset_version_id": dataset_version_id,
                            "universe_version_id": universe_version_id,
                            "configuration_hash": config_hash,
                        },
                    )

            connection.execute(
                text(
                    "UPDATE dataset_versions SET immutable = TRUE, vintage_label = 'sealed' "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            )
            connection.execute(
                insert_experiment,
                {
                    "experiment_id": "EXP-SEALED-DATASET",
                    "strategy_version_id": strategy_version_id,
                    "dataset_version_id": dataset_version_id,
                    "universe_version_id": universe_version_id,
                    "configuration_hash": config_hash,
                },
            )

            assert connection.execute(
                text(
                    "SELECT dataset_version_id FROM experiments "
                    "WHERE experiment_id = 'EXP-SEALED-DATASET'"
                )
            ).scalar_one() == dataset_version_id
        finally:
            transaction.rollback()
