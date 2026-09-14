import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_data_manifest import (
    SqlAlchemyMarketDataCoverageManifestRepository,
)


@pytest.mark.integration
def test_market_data_manifest_is_durable_idempotent_and_conflict_rejecting() -> None:
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
            dataset_id = uuid4()
            version_id = uuid4()
            late_version_id = uuid4()
            t0 = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
            t1 = t0 + timedelta(minutes=1)
            identity_map = {("TEST", "ABC"): instrument_id}

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id, "symbol": f"MANIFEST-{instrument_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                    "VALUES (:dataset_id, :name, 'TEST', TRUE)"
                ),
                {"dataset_id": dataset_id, "name": f"manifest-{dataset_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions("
                    "dataset_version_id, dataset_id, version, vintage_label, immutable"
                    ") VALUES "
                    "(:version_id, :dataset_id, 'v1', 'staging', FALSE), "
                    "(:late_version_id, :dataset_id, 'v2', 'staging', FALSE)"
                ),
                {
                    "version_id": version_id,
                    "late_version_id": late_version_id,
                    "dataset_id": dataset_id,
                },
            )

            repo = SqlAlchemyMarketDataCoverageManifestRepository(connection)
            declared = (_request(t0), _request(t1))
            repo.declare(version_id, declared, identity_map=identity_map)
            repo.declare(version_id, declared, identity_map=identity_map)

            stored = connection.execute(
                text(
                    "SELECT manifest_hash, manifest FROM market_data_coverage_manifests "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": version_id},
            ).mappings().one()
            assert len(stored["manifest_hash"]) == 64
            assert stored["manifest"]["version"] == 1
            assert len(stored["manifest"]["windows"]) == 2

            with pytest.raises(ValueError, match="MARKET_DATA_MANIFEST_CONFLICT"):
                repo.declare(
                    version_id,
                    (_request(t0),),
                    identity_map=identity_map,
                )

            with pytest.raises(IntegrityError, match="MARKET_DATA_MANIFEST_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE market_data_coverage_manifests "
                            "SET manifest_hash = :hash WHERE dataset_version_id = :version_id"
                        ),
                        {"hash": "0" * 64, "version_id": version_id},
                    )

            connection.execute(
                text(
                    "INSERT INTO market_bars("
                    "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                    "open, high, low, close, volume) VALUES ("
                    ":version_id, :instrument_id, :event_time, :event_time, :event_time, "
                    "100, 101, 99, 100, 1000)"
                ),
                {
                    "version_id": late_version_id,
                    "instrument_id": instrument_id,
                    "event_time": t0,
                },
            )
            with pytest.raises(ValueError, match="REQUIRES_EMPTY_STAGING_VERSION"):
                repo.declare(
                    late_version_id,
                    (_request(t0),),
                    identity_map=identity_map,
                )
        finally:
            transaction.rollback()


def _request(start: datetime) -> MarketDataRequest:
    return MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
