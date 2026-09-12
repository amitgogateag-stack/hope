import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_experiment_invalidations_are_append_only() -> None:
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
            config_hash = "i" * 64
            experiment_id = "EXP-INVALIDATION-IMMUTABLE"

            connection.execute(text(
                "INSERT INTO strategies(strategy_id, name, family) VALUES (:id, :name, 'TEST')"
            ), {"id": strategy_id, "name": f"invalidation-{strategy_id}"})
            connection.execute(text(
                "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                "VALUES (:vid, :sid, '1.0.0', 'commit-a')"
            ), {"vid": strategy_version_id, "sid": strategy_id})
            connection.execute(text(
                "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                "VALUES (:id, :name, 'TEST', TRUE)"
            ), {"id": dataset_id, "name": f"invalidation-{dataset_id}"})
            connection.execute(text(
                "INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label, immutable) "
                "VALUES (:vid, :did, 'v1', 'sealed', TRUE)"
            ), {"vid": dataset_version_id, "did": dataset_id})
            connection.execute(text(
                "INSERT INTO universes(universe_id, name) VALUES (:id, :name)"
            ), {"id": universe_id, "name": f"invalidation-{universe_id}"})
            connection.execute(text(
                "INSERT INTO universe_versions(universe_version_id, universe_id, version, pit_certified, declared_member_count) "
                "VALUES (:vid, :uid, 'v1', TRUE, 0)"
            ), {"vid": universe_version_id, "uid": universe_id})
            connection.execute(text(
                "INSERT INTO configuration_snapshots(configuration_hash, canonical_json) "
                "VALUES (:hash, CAST(:payload AS JSONB))"
            ), {"hash": config_hash, "payload": "{}"})
            connection.execute(text(
                "INSERT INTO experiments(experiment_id, hypothesis, strategy_version_id, dataset_version_id, "
                "universe_version_id, configuration_hash, environment, status) "
                "VALUES (:experiment_id, 'invalidate immutable history', :sid, :did, :uid, :hash, 'RESEARCH', 'CREATED')"
            ), {
                "experiment_id": experiment_id,
                "sid": strategy_version_id,
                "did": dataset_version_id,
                "uid": universe_version_id,
                "hash": config_hash,
            })
            connection.execute(text(
                "INSERT INTO experiment_invalidations(experiment_id, reason) VALUES (:experiment_id, 'bad data')"
            ), {"experiment_id": experiment_id})

            with pytest.raises(IntegrityError, match="EXPERIMENT_INVALIDATION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(text(
                        "UPDATE experiment_invalidations SET reason = 'rewritten reason' "
                        "WHERE experiment_id = :experiment_id"
                    ), {"experiment_id": experiment_id})

            with pytest.raises(IntegrityError, match="EXPERIMENT_INVALIDATION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(text(
                        "DELETE FROM experiment_invalidations WHERE experiment_id = :experiment_id"
                    ), {"experiment_id": experiment_id})

            stored = connection.execute(text(
                "SELECT reason FROM experiment_invalidations WHERE experiment_id = :experiment_id"
            ), {"experiment_id": experiment_id}).scalar_one()
            assert stored == "bad data"
        finally:
            transaction.rollback()
