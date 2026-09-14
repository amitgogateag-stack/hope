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
def test_market_data_manifest_binds_pit_universe_and_freezes_membership() -> None:
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
            invalid_version_id = uuid4()
            late_version_id = uuid4()
            universe_id = uuid4()
            universe_version_id = uuid4()
            t0 = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
            t1 = t0 + timedelta(minutes=1)
            t2 = t1 + timedelta(minutes=1)
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
                    "INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label, immutable) "
                    "VALUES (:v1, :dataset_id, 'v1', 'staging', FALSE), "
                    "(:v2, :dataset_id, 'v2', 'staging', FALSE), "
                    "(:v3, :dataset_id, 'v3', 'staging', FALSE)"
                ),
                {"v1": version_id, "v2": invalid_version_id, "v3": late_version_id, "dataset_id": dataset_id},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:id, :name)"),
                {"id": universe_id, "name": f"manifest-universe-{universe_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_versions(universe_version_id, universe_id, version, pit_certified, declared_member_count) "
                    "VALUES (:version_id, :universe_id, 'v1', TRUE, 1)"
                ),
                {"version_id": universe_version_id, "universe_id": universe_id},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_members(universe_version_id, instrument_id, valid_from, valid_to) "
                    "VALUES (:universe_version_id, :instrument_id, :valid_from, :valid_to)"
                ),
                {
                    "universe_version_id": universe_version_id,
                    "instrument_id": instrument_id,
                    "valid_from": t0,
                    "valid_to": t2,
                },
            )

            repo = SqlAlchemyMarketDataCoverageManifestRepository(connection)
            declared = (_request(t0), _request(t1))
            repo.declare(version_id, universe_version_id, declared, identity_map=identity_map)
            repo.declare(version_id, universe_version_id, declared, identity_map=identity_map)

            stored = connection.execute(
                text(
                    "SELECT universe_version_id, manifest_hash, manifest FROM market_data_coverage_manifests "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": version_id},
            ).mappings().one()
            assert stored["universe_version_id"] == universe_version_id
            assert len(stored["manifest_hash"]) == 64
            assert stored["manifest"]["version"] == 2
            assert stored["manifest"]["universe_version_id"] == str(universe_version_id)

            with pytest.raises(ValueError, match="MARKET_DATA_MANIFEST_CONFLICT"):
                repo.declare(
                    version_id,
                    universe_version_id,
                    (_request(t0),),
                    identity_map=identity_map,
                )

            with pytest.raises(ValueError, match="REQUEST_OUTSIDE_UNIVERSE_MEMBERSHIP"):
                repo.declare(
                    invalid_version_id,
                    universe_version_id,
                    (_request(t2),),
                    identity_map=identity_map,
                )

            savepoint = connection.begin_nested()
            try:
                with pytest.raises(IntegrityError, match="MARKET_DATA_MANIFEST_UNIVERSE_MEMBERSHIP_IMMUTABLE"):
                    connection.execute(
                        text(
                            "UPDATE universe_members SET valid_to = :new_valid_to "
                            "WHERE universe_version_id = :universe_version_id AND instrument_id = :instrument_id"
                        ),
                        {
                            "new_valid_to": t2 + timedelta(minutes=1),
                            "universe_version_id": universe_version_id,
                            "instrument_id": instrument_id,
                        },
                    )
            finally:
                savepoint.rollback()

            connection.execute(
                text(
                    "INSERT INTO market_bars(dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                    "open, high, low, close, volume) VALUES "
                    "(:version_id, :instrument_id, :event_time, :event_time, :event_time, 100, 101, 99, 100, 1000)"
                ),
                {"version_id": late_version_id, "instrument_id": instrument_id, "event_time": t0},
            )
            with pytest.raises(ValueError, match="REQUIRES_EMPTY_STAGING_VERSION"):
                repo.declare(
                    late_version_id,
                    universe_version_id,
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
