import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_experiment_referenced_universe_version_metadata_is_immutable(operation: str) -> None:
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
            universe_id = uuid4()
            universe_version_id = uuid4()
            experiment_id = uuid4()
            strategy_version_id = uuid4()
            dataset_version_id = uuid4()
            configuration_snapshot_id = uuid4()

            connection.execute(text("INSERT INTO universes(universe_id, name) VALUES (:id, 'u')"), {"id": universe_id})
            connection.execute(text("INSERT INTO universe_versions(universe_version_id, universe_id, version, pit_certified, declared_member_count) VALUES (:vid, :uid, 'v1', TRUE, 0)"), {"vid": universe_version_id, "uid": universe_id})
            connection.execute(text("INSERT INTO strategies(strategy_id, name) VALUES (:id, 's')"), {"id": uuid4()})
            strategy_id = connection.execute(text("SELECT strategy_id FROM strategies WHERE name='s'")).scalar_one()
            connection.execute(text("INSERT INTO strategy_versions(strategy_version_id, strategy_id, version) VALUES (:id, :sid, 'v1')"), {"id": strategy_version_id, "sid": strategy_id})
            connection.execute(text("INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, source) VALUES (:id, :did, 'v1', 'test')"), {"id": dataset_version_id, "did": uuid4()})
            connection.execute(text("INSERT INTO configuration_snapshots(configuration_snapshot_id, configuration_hash, configuration_json) VALUES (:id, :hash, '{}')"), {"id": configuration_snapshot_id, "hash": "a" * 64})
            connection.execute(text("INSERT INTO experiments(experiment_id, strategy_version_id, dataset_version_id, universe_version_id, configuration_snapshot_id, hypothesis, status) VALUES (:eid, :sid, :did, :uid, :cid, 'h', 'DRAFT')"), {"eid": experiment_id, "sid": strategy_version_id, "did": dataset_version_id, "uid": universe_version_id, "cid": configuration_snapshot_id})

            statement = (
                "UPDATE universe_versions SET version = 'v2' WHERE universe_version_id = :id"
                if operation == "update"
                else "DELETE FROM universe_versions WHERE universe_version_id = :id"
            )
            with pytest.raises(IntegrityError, match="EXPERIMENT_UNIVERSE_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(text(statement), {"id": universe_version_id})
        finally:
            transaction.rollback()
