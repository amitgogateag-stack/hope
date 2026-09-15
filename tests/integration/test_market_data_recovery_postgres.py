import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import MarketDataRequest, ProviderMarketDataBatch
from hope.infrastructure.market_data.recovery import ingest_market_data_recovery_windows
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from hope.infrastructure.repositories.market_data_finalizer import SqlAlchemyMarketDataVersionFinalizer
from hope.infrastructure.repositories.market_data_manifest import (
    SqlAlchemyMarketDataCoverageManifestRepository,
)
from hope.infrastructure.repositories.market_data_recovery import (
    SqlAlchemyMarketDataRecoveryAuthorizer,
)


class DeterministicProvider:
    def __init__(self, *, fail_on_start: datetime | None = None) -> None:
        self.calls: list[MarketDataRequest] = []
        self._fail_on_start = fail_on_start

    @property
    def source(self) -> str:
        return "TEST"

    def fetch_bars(self, request: MarketDataRequest) -> ProviderMarketDataBatch:
        self.calls.append(request)
        if request.start == self._fail_on_start:
            raise RuntimeError("SIMULATED_PROVIDER_FAILURE")
        bars = tuple(
            RawMarketBar(
                source=request.source,
                source_symbol=symbol,
                event_time=event_time,
                available_time=event_time,
                ingestion_time=event_time,
                open="100",
                high="101",
                low="99",
                close="100",
                volume="1000",
            )
            for symbol, event_time in request.expected_keys
        )
        return ProviderMarketDataBatch(
            source=request.source,
            request=request,
            bars=bars,
            fetched_at=request.end,
        )


@pytest.mark.integration
def test_recovery_restart_is_durable_idempotent_and_only_full_lifecycle_seals() -> None:
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
            universe_id = uuid4()
            universe_version_id = uuid4()
            t0 = datetime(2026, 9, 15, 14, 30, tzinfo=timezone.utc)
            requests = (_request(t0), _request(t0 + timedelta(minutes=1)))
            identity_map = {("TEST", "ABC"): instrument_id}

            _seed_staging_version(
                connection,
                instrument_id=instrument_id,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
                universe_id=universe_id,
                universe_version_id=universe_version_id,
                valid_from=t0,
                valid_to=t0 + timedelta(minutes=2),
            )
            SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(
                dataset_version_id,
                universe_version_id,
                requests,
                identity_map=identity_map,
            )

            failing_provider = DeterministicProvider(fail_on_start=requests[1].start)
            with pytest.raises(RuntimeError, match="SIMULATED_PROVIDER_FAILURE"):
                ingest_market_data_recovery_windows(
                    failing_provider,
                    SqlAlchemyMarketBarSink(connection),
                    SqlAlchemyMarketDataRecoveryAuthorizer(connection),
                    dataset_version_id,
                    requests,
                    identity_map=identity_map,
                )

            assert _bar_count(connection, dataset_version_id) == 1
            assert _version_state(connection, dataset_version_id) == (False, "staging")

            retry_provider = DeterministicProvider()
            ingest_market_data_recovery_windows(
                retry_provider,
                SqlAlchemyMarketBarSink(connection),
                SqlAlchemyMarketDataRecoveryAuthorizer(connection),
                dataset_version_id,
                (requests[0],),
                identity_map=identity_map,
            )
            assert _bar_count(connection, dataset_version_id) == 1
            assert _version_state(connection, dataset_version_id) == (False, "staging")

            ingest_market_data_recovery_windows(
                retry_provider,
                SqlAlchemyMarketBarSink(connection),
                SqlAlchemyMarketDataRecoveryAuthorizer(connection),
                dataset_version_id,
                (requests[1],),
                identity_map=identity_map,
            )
            assert _bar_count(connection, dataset_version_id) == 2
            assert _version_state(connection, dataset_version_id) == (False, "staging")

            lifecycle_provider = DeterministicProvider()
            ingest_and_seal_market_data_windows(
                lifecycle_provider,
                SqlAlchemyMarketBarSink(connection),
                SqlAlchemyMarketDataVersionFinalizer(connection),
                dataset_version_id,
                requests,
                identity_map=identity_map,
            )
            assert lifecycle_provider.calls == list(requests)
            assert _bar_count(connection, dataset_version_id) == 2
            assert _version_state(connection, dataset_version_id) == (True, "sealed")

            post_seal_provider = DeterministicProvider()
            with pytest.raises(ValueError, match="MARKET_DATA_RECOVERY_REQUIRES_STAGING_VERSION"):
                ingest_market_data_recovery_windows(
                    post_seal_provider,
                    SqlAlchemyMarketBarSink(connection),
                    SqlAlchemyMarketDataRecoveryAuthorizer(connection),
                    dataset_version_id,
                    (requests[0],),
                    identity_map=identity_map,
                )
            assert post_seal_provider.calls == []
        finally:
            transaction.rollback()


@pytest.mark.integration
def test_recovery_preflight_rejects_undeclared_subset_before_provider_access() -> None:
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
            universe_id = uuid4()
            universe_version_id = uuid4()
            t0 = datetime(2026, 9, 15, 15, 30, tzinfo=timezone.utc)
            declared = (_request(t0),)
            identity_map = {("TEST", "ABC"): instrument_id}

            _seed_staging_version(
                connection,
                instrument_id=instrument_id,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
                universe_id=universe_id,
                universe_version_id=universe_version_id,
                valid_from=t0,
                valid_to=t0 + timedelta(minutes=2),
            )
            SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(
                dataset_version_id,
                universe_version_id,
                declared,
                identity_map=identity_map,
            )

            provider = DeterministicProvider()
            with pytest.raises(
                ValueError,
                match="MARKET_DATA_RECOVERY_REQUEST_OUTSIDE_DECLARED_MANIFEST",
            ):
                ingest_market_data_recovery_windows(
                    provider,
                    SqlAlchemyMarketBarSink(connection),
                    SqlAlchemyMarketDataRecoveryAuthorizer(connection),
                    dataset_version_id,
                    (_request(t0 + timedelta(minutes=1)),),
                    identity_map=identity_map,
                )
            assert provider.calls == []
            assert _bar_count(connection, dataset_version_id) == 0
            assert _version_state(connection, dataset_version_id) == (False, "staging")
        finally:
            transaction.rollback()


def _seed_staging_version(
    connection,
    *,
    instrument_id,
    dataset_id,
    dataset_version_id,
    universe_id,
    universe_version_id,
    valid_from: datetime,
    valid_to: datetime,
) -> None:
    connection.execute(
        text(
            "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
            "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
        ),
        {"instrument_id": instrument_id, "symbol": f"RECOVERY-{instrument_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
            "VALUES (:dataset_id, :name, 'TEST', TRUE)"
        ),
        {"dataset_id": dataset_id, "name": f"recovery-{dataset_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO dataset_versions("
            "dataset_version_id, dataset_id, version, vintage_label, immutable"
            ") VALUES (:version_id, :dataset_id, 'v1', 'staging', FALSE)"
        ),
        {"version_id": dataset_version_id, "dataset_id": dataset_id},
    )
    connection.execute(
        text("INSERT INTO universes(universe_id, name) VALUES (:id, :name)"),
        {"id": universe_id, "name": f"recovery-universe-{universe_id}"},
    )
    connection.execute(
        text(
            "INSERT INTO universe_versions("
            "universe_version_id, universe_id, version, pit_certified, declared_member_count"
            ") VALUES (:version_id, :universe_id, 'v1', TRUE, 1)"
        ),
        {"version_id": universe_version_id, "universe_id": universe_id},
    )
    connection.execute(
        text(
            "INSERT INTO universe_members("
            "universe_version_id, instrument_id, valid_from, valid_to"
            ") VALUES (:version_id, :instrument_id, :valid_from, :valid_to)"
        ),
        {
            "version_id": universe_version_id,
            "instrument_id": instrument_id,
            "valid_from": valid_from,
            "valid_to": valid_to,
        },
    )


def _request(start: datetime) -> MarketDataRequest:
    return MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )


def _bar_count(connection, dataset_version_id) -> int:
    return connection.execute(
        text(
            "SELECT count(*) FROM market_bars "
            "WHERE dataset_version_id = :version_id"
        ),
        {"version_id": dataset_version_id},
    ).scalar_one()


def _version_state(connection, dataset_version_id) -> tuple[bool, str]:
    row = connection.execute(
        text(
            "SELECT immutable, vintage_label FROM dataset_versions "
            "WHERE dataset_version_id = :version_id"
        ),
        {"version_id": dataset_version_id},
    ).one()
    return row.immutable, row.vintage_label
