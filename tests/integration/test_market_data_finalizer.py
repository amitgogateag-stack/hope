import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_data_finalizer import (
    SqlAlchemyMarketDataVersionFinalizer,
)
from hope.infrastructure.repositories.market_data_manifest import (
    SqlAlchemyMarketDataCoverageManifestRepository,
)


@pytest.mark.integration
def test_market_data_finalizer_uses_manifest_not_current_request_subset() -> None:
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
            undeclared_version_id = uuid4()
            t0 = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
            t1 = t0 + timedelta(minutes=1)
            full_requests = (_request(t0), _request(t1))
            identity_map = {("TEST", "ABC"): instrument_id}

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id, "symbol": f"FINALIZE-{instrument_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                    "VALUES (:dataset_id, :name, 'TEST', TRUE)"
                ),
                {"dataset_id": dataset_id, "name": f"finalize-{dataset_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions("
                    "dataset_version_id, dataset_id, version, vintage_label, immutable"
                    ") VALUES "
                    "(:version_id, :dataset_id, 'v1', 'staging', FALSE), "
                    "(:undeclared, :dataset_id, 'v2', 'staging', FALSE)"
                ),
                {
                    "version_id": version_id,
                    "undeclared": undeclared_version_id,
                    "dataset_id": dataset_id,
                },
            )

            manifest = SqlAlchemyMarketDataCoverageManifestRepository(connection)
            manifest.declare(version_id, full_requests, identity_map=identity_map)
            finalizer = SqlAlchemyMarketDataVersionFinalizer(connection)

            _insert_bar(connection, version_id, instrument_id, t0)
            with pytest.raises(ValueError, match="PERSISTED_COVERAGE_MISSING"):
                finalizer.finalize(
                    version_id,
                    (_request(t0),),
                    identity_map=identity_map,
                )

            staging = connection.execute(
                text(
                    "SELECT immutable, vintage_label FROM dataset_versions "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": version_id},
            ).mappings().one()
            assert staging["immutable"] is False
            assert staging["vintage_label"] == "staging"

            with pytest.raises(ValueError, match="COVERAGE_MANIFEST_REQUIRED"):
                finalizer.finalize(
                    undeclared_version_id,
                    (_request(t0),),
                    identity_map=identity_map,
                )

            _insert_bar(connection, version_id, instrument_id, t1)
            finalizer.finalize(
                version_id,
                (_request(t1),),
                identity_map=identity_map,
            )
            finalizer.finalize(
                version_id,
                (_request(t1),),
                identity_map=identity_map,
            )

            sealed = connection.execute(
                text(
                    "SELECT immutable, vintage_label FROM dataset_versions "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": version_id},
            ).mappings().one()
            assert sealed["immutable"] is True
            assert sealed["vintage_label"] == "sealed"
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


def _insert_bar(connection, version_id, instrument_id, event_time: datetime) -> None:
    connection.execute(
        text(
            "INSERT INTO market_bars("
            "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
            "open, high, low, close, volume) VALUES ("
            ":version_id, :instrument_id, :event_time, :event_time, :event_time, "
            "100, 101, 99, 100, 1000)"
        ),
        {
            "version_id": version_id,
            "instrument_id": instrument_id,
            "event_time": event_time,
        },
    )
