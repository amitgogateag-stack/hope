import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.dataset_versions import SqlAlchemyMarketDataVersionSealer


@pytest.mark.integration
def test_legacy_market_data_version_sealer_cannot_bypass_manifest() -> None:
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
            instrument_id = uuid4()
            pit_dataset_id = uuid4()
            non_pit_dataset_id = uuid4()
            good_version_id = uuid4()
            empty_version_id = uuid4()
            non_pit_version_id = uuid4()
            fake_sealed_empty_version_id = uuid4()
            fake_sealed_non_pit_version_id = uuid4()
            t0 = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id, "symbol": f"SEAL-{instrument_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) VALUES "
                    "(:pit_id, :pit_name, 'TEST', TRUE), "
                    "(:non_pit_id, :non_pit_name, 'TEST', FALSE)"
                ),
                {
                    "pit_id": pit_dataset_id,
                    "pit_name": f"pit-{pit_dataset_id}",
                    "non_pit_id": non_pit_dataset_id,
                    "non_pit_name": f"non-pit-{non_pit_dataset_id}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label, immutable) VALUES "
                    "(:good, :pit_id, 'good', 'staging', FALSE), "
                    "(:empty, :pit_id, 'empty', 'staging', FALSE), "
                    "(:non_pit, :non_pit_id, 'non-pit', 'staging', FALSE), "
                    "(:fake_empty, :pit_id, 'fake-empty', 'sealed', TRUE), "
                    "(:fake_non_pit, :non_pit_id, 'fake-non-pit', 'sealed', TRUE)"
                ),
                {
                    "good": good_version_id,
                    "empty": empty_version_id,
                    "non_pit": non_pit_version_id,
                    "fake_empty": fake_sealed_empty_version_id,
                    "fake_non_pit": fake_sealed_non_pit_version_id,
                    "pit_id": pit_dataset_id,
                    "non_pit_id": non_pit_dataset_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO market_bars("
                    "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                    "open, high, low, close, volume) VALUES ("
                    ":version_id, :instrument_id, :t0, :t0, :t0, 100, 101, 99, 100, 1000)"
                ),
                {"version_id": good_version_id, "instrument_id": instrument_id, "t0": t0},
            )

            sealer = SqlAlchemyMarketDataVersionSealer(connection)

            with pytest.raises(ValueError, match="MARKET_DATA_DATASET_VERSION_EMPTY"):
                sealer.seal(empty_version_id)
            with pytest.raises(ValueError, match="MARKET_DATA_DATASET_NOT_PIT_CERTIFIED"):
                sealer.seal(non_pit_version_id)
            with pytest.raises(ValueError, match="MARKET_DATA_DATASET_VERSION_EMPTY"):
                sealer.seal(fake_sealed_empty_version_id)
            with pytest.raises(ValueError, match="MARKET_DATA_DATASET_NOT_PIT_CERTIFIED"):
                sealer.seal(fake_sealed_non_pit_version_id)

            with pytest.raises(ValueError, match="MANIFEST_BACKED_FINALIZER_REQUIRED"):
                sealer.seal(good_version_id)
            with pytest.raises(ValueError, match="MANIFEST_BACKED_FINALIZER_REQUIRED"):
                sealer.seal(good_version_id)

            sealed = connection.execute(
                text(
                    "SELECT immutable, vintage_label FROM dataset_versions "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": good_version_id},
            ).mappings().one()
            assert sealed["immutable"] is False
            assert sealed["vintage_label"] == "staging"
        finally:
            transaction.rollback()
