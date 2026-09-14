import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_data_coverage import (
    SqlAlchemyMarketDataCoverageVerifier,
)


@pytest.mark.integration
def test_persisted_market_data_coverage_rejects_missing_extra_and_duplicate_keys() -> None:
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
            dataset_version_id = uuid4()
            duplicate_version_id = uuid4()
            t0 = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
            t1 = t0 + timedelta(minutes=1)
            t2 = t1 + timedelta(minutes=1)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id, "symbol": f"COVERAGE-{instrument_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                    "VALUES (:dataset_id, :name, 'TEST', TRUE)"
                ),
                {"dataset_id": dataset_id, "name": f"coverage-{dataset_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions("
                    "dataset_version_id, dataset_id, version, vintage_label, immutable"
                    ") VALUES "
                    "(:version_id, :dataset_id, 'v1', 'staging', FALSE), "
                    "(:duplicate_version_id, :dataset_id, 'v2', 'staging', FALSE)"
                ),
                {
                    "version_id": dataset_version_id,
                    "duplicate_version_id": duplicate_version_id,
                    "dataset_id": dataset_id,
                },
            )

            requests = (
                _request(t0),
                _request(t1),
            )
            identity_map = {("TEST", "ABC"): instrument_id}
            verifier = SqlAlchemyMarketDataCoverageVerifier(connection)

            _insert_bar(connection, dataset_version_id, instrument_id, t0, t0, t0)
            with pytest.raises(ValueError, match="PERSISTED_COVERAGE_MISSING"):
                verifier.verify(
                    dataset_version_id,
                    requests,
                    identity_map=identity_map,
                )

            _insert_bar(connection, dataset_version_id, instrument_id, t1, t1, t1)
            verifier.verify(
                dataset_version_id,
                requests,
                identity_map=identity_map,
            )

            _insert_bar(connection, dataset_version_id, instrument_id, t2, t2, t2)
            with pytest.raises(ValueError, match="PERSISTED_COVERAGE_EXTRA"):
                verifier.verify(
                    dataset_version_id,
                    requests,
                    identity_map=identity_map,
                )

            _insert_bar(connection, duplicate_version_id, instrument_id, t0, t0, t0)
            _insert_bar(connection, duplicate_version_id, instrument_id, t1, t1, t1)
            shifted = t0 + timedelta(seconds=1)
            _insert_bar(
                connection,
                duplicate_version_id,
                instrument_id,
                t0,
                shifted,
                shifted,
            )
            with pytest.raises(ValueError, match="DUPLICATE_LOGICAL_KEY"):
                verifier.verify(
                    duplicate_version_id,
                    requests,
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


def _insert_bar(
    connection,
    dataset_version_id,
    instrument_id,
    event_time: datetime,
    available_time: datetime,
    ingestion_time: datetime,
) -> None:
    connection.execute(
        text(
            "INSERT INTO market_bars("
            "dataset_version_id, instrument_id, event_time, available_time, "
            "effective_time, ingestion_time, open, high, low, close, volume"
            ") VALUES ("
            ":version_id, :instrument_id, :event_time, :available_time, NULL, "
            ":ingestion_time, 100, 101, 99, 100, 1000)"
        ),
        {
            "version_id": dataset_version_id,
            "instrument_id": instrument_id,
            "event_time": event_time,
            "available_time": available_time,
            "ingestion_time": ingestion_time,
        },
    )
