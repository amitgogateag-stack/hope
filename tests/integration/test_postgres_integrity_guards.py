import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_postgres_migrations_and_integrity_guards() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    # Keep schema/migrations committed, but isolate all test data in a
    # transaction that is rolled back at the end of the test.
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            checksum_count = connection.execute(
                text("SELECT count(*) FROM hope_schema_migrations WHERE checksum IS NOT NULL")
            ).scalar_one()
            migration_count = len(list(migrations_dir.glob("*.sql")))
            assert checksum_count == migration_count

            instrument_id = uuid4()
            instrument2_id = uuid4()
            broker_id = f"broker-{uuid4()}"
            broker2_id = f"broker-{uuid4()}"
            signal_id = uuid4()
            strategy_id = uuid4()
            dataset_id = uuid4()
            dataset_version_id = uuid4()
            universe_id = uuid4()
            universe_version_id = uuid4()
            configuration_hash = "a" * 64
            strategy_version_id = uuid4()

            connection.execute(text("""
                INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status)
                VALUES (:id, 'TEST', 'TESTEX', 'ACTIVE'),
                       (:id2, 'TEST2', 'TESTEX', 'ACTIVE')
            """), {"id": instrument_id, "id2": instrument2_id})

            connection.execute(text("""
                INSERT INTO broker_instruments(broker_instrument_id, broker, instrument_id, status)
                VALUES (:bid, 'TEST_BROKER', :id, 'ACTIVE'),
                       (:bid2, 'TEST_BROKER', :id2, 'ACTIVE')
            """), {
                "bid": broker_id,
                "bid2": broker2_id,
                "id": instrument_id,
                "id2": instrument2_id,
            })

            # Same canonical instrument may have an active identity at a different broker.
            connection.execute(text("""
                INSERT INTO broker_instruments(broker_instrument_id, broker, instrument_id, status)
                VALUES (:bid, 'SECOND_BROKER', :id, 'ACTIVE')
            """), {"bid": f"broker-{uuid4()}", "id": instrument_id})

            # But two active canonical identities for the same broker are forbidden.
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("""
                        INSERT INTO broker_instruments(broker_instrument_id, broker, instrument_id, status)
                        VALUES (:bid, 'TEST_BROKER', :id, 'ACTIVE')
                    """), {"bid": f"broker-{uuid4()}", "id": instrument_id})

            connection.execute(text("""
                INSERT INTO strategies(strategy_id, name, family)
                VALUES (:id, 'PG Integrity Test', 'TEST')
            """), {"id": strategy_id})

            connection.execute(text("""
                INSERT INTO strategy_versions(strategy_version_id, strategy_id, version, code_commit)
                VALUES (:id, :strategy_id, '1', 'test')
            """), {"id": strategy_version_id, "strategy_id": strategy_id})

            connection.execute(text("""
                INSERT INTO datasets(dataset_id, name, source, pit_certified)
                VALUES (:id, 'PG Integrity Dataset', 'test', TRUE)
            """), {"id": dataset_id})

            connection.execute(text("""
                INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label)
                VALUES (:id, :dataset_id, '1', 'test')
            """), {"id": dataset_version_id, "dataset_id": dataset_id})

            connection.execute(text("""
                INSERT INTO universes(universe_id, name)
                VALUES (:id, 'PG Integrity Universe')
            """), {"id": universe_id})

            connection.execute(text("""
                INSERT INTO universe_versions(
                    universe_version_id,
                    universe_id,
                    version,
                    declared_member_count,
                    pit_certified
                )
                VALUES (:id, :universe_id, '1', 1, TRUE)
            """), {"id": universe_version_id, "universe_id": universe_id})

            connection.execute(text("""
                INSERT INTO universe_members(universe_version_id, instrument_id)
                VALUES (:universe_version_id, :instrument_id)
            """), {
                "universe_version_id": universe_version_id,
                "instrument_id": instrument_id,
            })

            connection.execute(text("""
                INSERT INTO configuration_snapshots(configuration_hash, canonical_json)
                VALUES (:hash, '{}'::jsonb)
            """), {"hash": configuration_hash})

            connection.execute(text("""
                INSERT INTO experiments(
                    experiment_id,
                    hypothesis,
                    strategy_version_id,
                    dataset_version_id,
                    universe_version_id,
                    configuration_hash,
                    environment
                )
                VALUES (
                    'pg-integrity',
                    'guard test',
                    :strategy_version_id,
                    :dataset_version_id,
                    :universe_version_id,
                    :configuration_hash,
                    'BACKTEST'
                )
            """), {
                "strategy_version_id": strategy_version_id,
                "dataset_version_id": dataset_version_id,
                "universe_version_id": universe_version_id,
                "configuration_hash": configuration_hash,
            })

            connection.execute(text("""
                INSERT INTO signals(
                    signal_id,
                    experiment_id,
                    instrument_id,
                    decision_time,
                    state
                )
                VALUES (:signal_id, 'pg-integrity', :instrument_id, now(), 'SIGNAL')
            """), {
                "signal_id": signal_id,
                "instrument_id": instrument_id,
            })

            # Signal/order instrument mismatch must be rejected by the database trigger.
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("""
                        INSERT INTO orders(
                            signal_id,
                            instrument_id,
                            environment,
                            side,
                            quantity
                        )
                        VALUES (:signal_id, :instrument_id, 'PAPER', 'BUY', 1)
                    """), {
                        "signal_id": signal_id,
                        "instrument_id": instrument2_id,
                    })
        finally:
            transaction.rollback()
