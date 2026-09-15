import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_data_finalizer import SqlAlchemyMarketDataVersionFinalizer
from hope.infrastructure.repositories.market_data_manifest import (
    SqlAlchemyMarketDataCoverageManifestRepository,
    build_market_data_manifest,
)
from hope.infrastructure.repositories.market_data_recovery import (
    SqlAlchemyMarketDataRecoveryAuthorizer,
)


@pytest.mark.integration
def test_market_data_finalizer_uses_manifest_and_bound_universe() -> None:
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
            corrupt_manifest_version_id = uuid4()
            non_staging_version_id = uuid4()
            wrong_source_manifest_version_id = uuid4()
            outsider_manifest_version_id = uuid4()
            outsider_instrument_id = uuid4()
            universe_id = uuid4()
            universe_version_id = uuid4()
            t0 = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
            t1 = t0 + timedelta(minutes=1)
            full_requests = (_request(t0), _request(t1))
            identity_map = {("TEST", "ABC"): instrument_id}

            connection.execute(
                text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, :symbol, 'TEST', 'ACTIVE')"),
                {"id": instrument_id, "symbol": f"FINALIZE-{instrument_id}"},
            )
            connection.execute(
                text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, :symbol, 'TEST', 'ACTIVE')"),
                {"id": outsider_instrument_id, "symbol": f"OUTSIDER-{outsider_instrument_id}"},
            )
            connection.execute(
                text("INSERT INTO datasets(dataset_id, name, source, pit_certified) VALUES (:id, :name, 'TEST', TRUE)"),
                {"id": dataset_id, "name": f"finalize-{dataset_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label, immutable) VALUES "
                    "(:v1, :dataset_id, 'v1', 'staging', FALSE), "
                    "(:v2, :dataset_id, 'v2', 'staging', FALSE), "
                    "(:v3, :dataset_id, 'v3', 'staging', FALSE), "
                    "(:v4, :dataset_id, 'v4', 'draft', FALSE), "
                    "(:v5, :dataset_id, 'v5', 'staging', FALSE), "
                    "(:v6, :dataset_id, 'v6', 'staging', FALSE)"
                ),
                {
                    "v1": version_id,
                    "v2": undeclared_version_id,
                    "v3": corrupt_manifest_version_id,
                    "v4": non_staging_version_id,
                    "v5": wrong_source_manifest_version_id,
                    "v6": outsider_manifest_version_id,
                    "dataset_id": dataset_id,
                },
            )
            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:id, :name)"),
                {"id": universe_id, "name": f"finalizer-universe-{universe_id}"},
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
                    "valid_to": t1 + timedelta(minutes=1),
                },
            )
            outsider_request = _request(t0, symbol="OUT")
            outsider_identity_map = {("TEST", "OUT"): outsider_instrument_id}
            outsider_payload = build_market_data_manifest(
                universe_version_id,
                (outsider_request,),
                identity_map=outsider_identity_map,
            )
            outsider_canonical = json.dumps(
                outsider_payload,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                text(
                    "INSERT INTO market_data_coverage_manifests("
                    "dataset_version_id, universe_version_id, manifest_hash, manifest"
                    ") VALUES (:version_id, :universe_version_id, :manifest_hash, CAST(:manifest AS JSONB))"
                ),
                {
                    "version_id": outsider_manifest_version_id,
                    "universe_version_id": universe_version_id,
                    "manifest_hash": hashlib.sha256(
                        outsider_canonical.encode("utf-8")
                    ).hexdigest(),
                    "manifest": outsider_canonical,
                },
            )

            corrupt_payload = build_market_data_manifest(
                universe_version_id,
                (_request(t0),),
                identity_map=identity_map,
            )
            connection.execute(
                text(
                    "INSERT INTO market_data_coverage_manifests("
                    "dataset_version_id, universe_version_id, manifest_hash, manifest"
                    ") VALUES (:version_id, :universe_version_id, :manifest_hash, CAST(:manifest AS JSONB))"
                ),
                {
                    "version_id": corrupt_manifest_version_id,
                    "universe_version_id": universe_version_id,
                    "manifest_hash": "0" * 64,
                    "manifest": json.dumps(corrupt_payload),
                },
            )
            wrong_source_request = _request(t0, source="OTHER")
            wrong_source_identity_map = {("OTHER", "ABC"): instrument_id}
            wrong_source_payload = build_market_data_manifest(
                universe_version_id,
                (wrong_source_request,),
                identity_map=wrong_source_identity_map,
            )
            wrong_source_canonical = json.dumps(
                wrong_source_payload,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                text(
                    "INSERT INTO market_data_coverage_manifests("
                    "dataset_version_id, universe_version_id, manifest_hash, manifest"
                    ") VALUES (:version_id, :universe_version_id, :manifest_hash, CAST(:manifest AS JSONB))"
                ),
                {
                    "version_id": wrong_source_manifest_version_id,
                    "universe_version_id": universe_version_id,
                    "manifest_hash": hashlib.sha256(
                        wrong_source_canonical.encode("utf-8")
                    ).hexdigest(),
                    "manifest": wrong_source_canonical,
                },
            )

            manifest = SqlAlchemyMarketDataCoverageManifestRepository(connection)
            manifest.declare(
                version_id,
                universe_version_id,
                full_requests,
                identity_map=identity_map,
            )
            finalizer = SqlAlchemyMarketDataVersionFinalizer(connection)
            recovery = SqlAlchemyMarketDataRecoveryAuthorizer(connection)

            wider_cadence_request = MarketDataRequest(
                source="TEST",
                source_symbols=("ABC",),
                start=t0,
                end=t0 + timedelta(minutes=2),
                interval=timedelta(minutes=2),
            )
            with pytest.raises(ValueError, match="RECOVERY_REQUEST_CONTRACT_MISMATCH"):
                recovery.preflight_subset(
                    version_id,
                    (wider_cadence_request,),
                    identity_map=identity_map,
                )

            with pytest.raises(ValueError, match="FINALIZE_REQUIRES_STAGING_VERSION"):
                finalizer.finalize(
                    non_staging_version_id,
                    (_request(t0),),
                    identity_map=identity_map,
                )

            with pytest.raises(ValueError, match="MANIFEST_DATASET_SOURCE_MISMATCH"):
                finalizer.finalize(
                    wrong_source_manifest_version_id,
                    (wrong_source_request,),
                    identity_map=wrong_source_identity_map,
                )

            _insert_bar(
                connection,
                outsider_manifest_version_id,
                outsider_instrument_id,
                t0,
            )
            with pytest.raises(ValueError, match="INSTRUMENT_NOT_IN_UNIVERSE"):
                finalizer.preflight(
                    outsider_manifest_version_id,
                    (outsider_request,),
                    identity_map=outsider_identity_map,
                )
            with pytest.raises(ValueError, match="INSTRUMENT_NOT_IN_UNIVERSE"):
                finalizer.finalize(
                    outsider_manifest_version_id,
                    (outsider_request,),
                    identity_map=outsider_identity_map,
                )

            _insert_bar(connection, corrupt_manifest_version_id, instrument_id, t0)
            with pytest.raises(ValueError, match="MANIFEST_HASH_MISMATCH"):
                finalizer.finalize(corrupt_manifest_version_id, (_request(t0),), identity_map=identity_map)

            _insert_bar(connection, version_id, instrument_id, t0)
            with pytest.raises(ValueError, match="PERSISTED_COVERAGE_MISSING"):
                finalizer.finalize(version_id, (_request(t0),), identity_map=identity_map)

            with pytest.raises(ValueError, match="COVERAGE_MANIFEST_REQUIRED"):
                finalizer.finalize(undeclared_version_id, (_request(t0),), identity_map=identity_map)

            _insert_bar(connection, version_id, instrument_id, t1)
            finalizer.finalize(version_id, (_request(t1),), identity_map=identity_map)
            finalizer.finalize(version_id, (_request(t1),), identity_map=identity_map)

            sealed = connection.execute(
                text("SELECT immutable, vintage_label FROM dataset_versions WHERE dataset_version_id = :version_id"),
                {"version_id": version_id},
            ).mappings().one()
            assert sealed["immutable"] is True
            assert sealed["vintage_label"] == "sealed"
        finally:
            transaction.rollback()


def _request(
    start: datetime,
    *,
    source: str = "TEST",
    symbol: str = "ABC",
) -> MarketDataRequest:
    return MarketDataRequest(
        source=source,
        source_symbols=(symbol,),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )


def _insert_bar(connection, version_id, instrument_id, event_time: datetime) -> None:
    connection.execute(
        text(
            "INSERT INTO market_bars(dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
            "open, high, low, close, volume) VALUES "
            "(:version_id, :instrument_id, :event_time, :event_time, :event_time, 100, 101, 99, 100, 1000)"
        ),
        {"version_id": version_id, "instrument_id": instrument_id, "event_time": event_time},
    )
