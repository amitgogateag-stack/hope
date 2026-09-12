import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_experiment_requires_declared_universe_cardinality_to_match_membership() -> None:
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
            strategy_id = uuid4()
            strategy_version_id = uuid4()
            dataset_id = uuid4()
            dataset_version_id = uuid4()
            universe_id = uuid4()
            universe_version_id = uuid4()
            instrument_id = uuid4()
            config_hash = "c" * 64

            connection.execute(text(
                "INSERT INTO strategies(strategy_id, name, family) VALUES (:id, :name, 'TEST')"
            ), {"id": strategy_id, "name": f"cardinality-{strategy_id}"})
            connection.execute(text(
                "INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit) "
                "VALUES (:vid, :sid, '1.0.0', 'commit-a')"
            ), {"vid": strategy_version_id, "sid": strategy_id})
            connection.execute(text(
                "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                "VALUES (:id, :name, 'TEST', TRUE)"
            ), {"id": dataset_id, "name": f"cardinality-{dataset_id}"})
            connection.execute(text(
                "INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label, immutable) "
                "VALUES (:vid, :did, 'v1', 'sealed', TRUE)"
            ), {"vid": dataset_version_id, "did": dataset_id})
            connection.execute(text(
                "INSERT INTO universes(universe_id, name) VALUES (:id, :name)"
            ), {"id": universe_id, "name": f"cardinality-{universe_id}"})
            connection.execute(text(
                "INSERT INTO universe_versions(universe_version_id, universe_id, version, pit_certified, declared_member_count) "
                "VALUES (:vid, :uid, 'v1', TRUE, 1)"
            ), {"vid": universe_version_id, "uid": universe_id})
            connection.execute(text(
                "INSERT INTO configuration_snapshots(configuration_hash, canonical_json) "
                "VALUES (:hash, CAST(:payload AS JSONB))"
            ), {"hash": config_hash, "payload": "{}"})

            experiment_sql = text(
                "INSERT INTO experiments(experiment_id, hypothesis, strategy_version_id, dataset_version_id, "
                "universe_version_id, configuration_hash, environment, status) "
                "VALUES (:experiment_id, 'freeze exact universe cardinality', :sid, :did, :uid, :hash, 'RESEARCH', 'CREATED')"
            )
            params = {
                "experiment_id": "EXP-UNIVERSE-CARDINALITY",
                "sid": strategy_version_id,
                "did": dataset_version_id,
                "uid": universe_version_id,
                "hash": config_hash,
            }

            with pytest.raises(IntegrityError, match="EXPERIMENT_UNIVERSE_MEMBER_COUNT_MISMATCH"):
                with connection.begin_nested():
                    connection.execute(experiment_sql, params)

            connection.execute(text(
                "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
            ), {"id": instrument_id, "symbol": f"UC-{instrument_id}"})
            connection.execute(text(
                "INSERT INTO universe_members(universe_version_id, instrument_id) VALUES (:vid, :iid)"
            ), {"vid": universe_version_id, "iid": instrument_id})

            connection.execute(experiment_sql, params)
            assert connection.execute(text(
                "SELECT count(*) FROM experiments WHERE experiment_id = :experiment_id"
            ), {"experiment_id": params["experiment_id"]}).scalar_one() == 1
        finally:
            transaction.rollback()
