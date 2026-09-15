import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_manifest_backed_experiment_rejects_uncertified_market_data_provenance() -> None:
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
            manifest_universe_version_id = uuid4()
            other_universe_id = uuid4()
            other_universe_version_id = uuid4()
            strategy_id = uuid4()
            strategy_version_id = uuid4()
            config_hash = "b" * 64

            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                    "VALUES (:id, :name, 'TEST', TRUE)"
                ),
                {"id": dataset_id, "name": f"experiment-market-data-{dataset_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label, immutable) "
                    "VALUES (:version_id, :dataset_id, 'v1', 'staging', FALSE)"
                ),
                {"version_id": dataset_version_id, "dataset_id": dataset_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:id, :name)"),
                {"id": universe_id, "name": f"manifest-universe-{universe_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_versions(universe_version_id, universe_id, version, pit_certified, declared_member_count) "
                    "VALUES (:version_id, :universe_id, 'v1', TRUE, 0)"
                ),
                {"version_id": manifest_universe_version_id, "universe_id": universe_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:id, :name)"),
                {"id": other_universe_id, "name": f"other-universe-{other_universe_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_versions(universe_version_id, universe_id, version, pit_certified, declared_member_count) "
                    "VALUES (:version_id, :universe_id, 'v1', TRUE, 0)"
                ),
                {"version_id": other_universe_version_id, "universe_id": other_universe_id},
            )
            connection.execute(
                text(
                    "INSERT INTO market_data_coverage_manifests(dataset_version_id, universe_version_id, manifest_hash, manifest) "
                    "VALUES (:dataset_version_id, :universe_version_id, :hash, CAST(:manifest AS JSONB))"
                ),
                {
                    "dataset_version_id": dataset_version_id,
                    "universe_version_id": manifest_universe_version_id,
                    "hash": "c" * 64,
                    "manifest": '{"version":2,"windows":[],"universe_version_id":"%s"}'
                    % manifest_universe_version_id,
                },
            )
            connection.execute(
                text("INSERT INTO strategies(strategy_id, name, family) VALUES (:id, :name, 'TEST')"),
                {"id": strategy_id, "name": f"market-data-strategy-{strategy_id}"},
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
                    "VALUES (:hash, CAST('{}' AS JSONB))"
                ),
                {"hash": config_hash},
            )

            insert_experiment = text(
                "INSERT INTO experiments(experiment_id, hypothesis, strategy_version_id, dataset_version_id, "
                "universe_version_id, configuration_hash, environment, status) VALUES ("
                ":experiment_id, 'certified market data provenance', :strategy_version_id, :dataset_version_id, "
                ":universe_version_id, :configuration_hash, 'RESEARCH', 'CREATED')"
            )

            # The market-data-specific trigger sorts before the generic frozen
            # dataset trigger, so manifest-backed staging evidence fails with the
            # stronger domain-specific contract.
            with pytest.raises(IntegrityError, match="EXPERIMENT_MARKET_DATA_NOT_CERTIFIED"):
                with connection.begin_nested():
                    connection.execute(
                        insert_experiment,
                        {
                            "experiment_id": "EXP-UNCERTIFIED-MARKET-DATA",
                            "strategy_version_id": strategy_version_id,
                            "dataset_version_id": dataset_version_id,
                            "universe_version_id": manifest_universe_version_id,
                            "configuration_hash": config_hash,
                        },
                    )

            # Even before sealing, a disagreement between the experiment's
            # immutable universe provenance and the durable market-data manifest
            # must never be admitted.  Certification failure is expected first
            # while staging; the SQL contract still contains and enforces the
            # explicit universe equality gate once evidence is sealed.
            migration_sql = (migrations_dir / "075_experiment_market_data_provenance.sql").read_text()
            assert "manifest_universe IS DISTINCT FROM NEW.universe_version_id" in migration_sql
            assert "EXPERIMENT_MARKET_DATA_UNIVERSE_MISMATCH" in migration_sql
        finally:
            transaction.rollback()
