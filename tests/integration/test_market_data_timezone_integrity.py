"""Real PostgreSQL coverage and restart proofs across daylight-saving transitions."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import MarketDataRequest, ProviderMarketDataBatch
from hope.infrastructure.market_data.recovery import ingest_market_data_recovery_windows
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from hope.infrastructure.repositories.market_data_finalizer import SqlAlchemyMarketDataVersionFinalizer
from hope.infrastructure.repositories.market_data_manifest import SqlAlchemyMarketDataCoverageManifestRepository
from hope.infrastructure.repositories.market_data_recovery import SqlAlchemyMarketDataRecoveryAuthorizer

NY = ZoneInfo("America/New_York")


class _Provider:
    source = "TEST"

    def fetch_bars(self, request):
        # Generate true UTC slots independently of request.expected_keys. Return
        # the provider's original local timestamps, including both fall folds.
        cursor, end = request.start.astimezone(UTC), request.end.astimezone(UTC)
        bars = []
        while cursor < end:
            local = cursor.astimezone(NY)
            bars.append(RawMarketBar(source="TEST", source_symbol="ABC",
                event_time=local, available_time=local, ingestion_time=request.end,
                open="100", high="101", low="99", close="100", volume="1000"))
            cursor += request.interval
        return ProviderMarketDataBatch("TEST", request, tuple(bars), request.end)


def _seed(connection, request):
    instrument, dataset, version, universe, universe_version = (uuid4() for _ in range(5))
    connection.execute(text("INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) VALUES (:id,:name,'TEST','ACTIVE')"),
        {"id": instrument, "name": f"TZ-{instrument}"})
    connection.execute(text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'TEST',TRUE)"),
        {"id": dataset, "name": f"TZ-{dataset}"})
    connection.execute(text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:id,:dataset,'v1','staging',FALSE)"),
        {"id": version, "dataset": dataset})
    connection.execute(text("INSERT INTO universes(universe_id,name) VALUES (:id,:name)"),
        {"id": universe, "name": f"TZ-{universe}"})
    connection.execute(text("INSERT INTO universe_versions(universe_version_id,universe_id,version,pit_certified,declared_member_count) VALUES (:id,:universe,'v1',TRUE,1)"),
        {"id": universe_version, "universe": universe})
    connection.execute(text("INSERT INTO universe_members(universe_version_id,instrument_id,valid_from,valid_to) VALUES (:id,:instrument,:start,:end)"),
        {"id": universe_version, "instrument": instrument, "start": request.start, "end": request.end})
    identity_map = {("TEST", "ABC"): instrument}
    SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(version, universe_version, (request,), identity_map=identity_map)
    return version, identity_map


@pytest.mark.integration
@pytest.mark.parametrize("transition,expected_count", [("fall", 4), ("spring", 2)])
@pytest.mark.parametrize("scenario", ["restart", "concurrent", "crash"])
def test_timezone_coverage_survives_retries_sealing_and_restart(transition, expected_count, scenario):
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c timezone=America/New_York"})
    day = (2026, 11, 1) if transition == "fall" else (2026, 3, 8)
    request = MarketDataRequest("TEST", ("ABC",), datetime(*day, 0, tzinfo=NY),
        datetime(*day, 3, tzinfo=NY), timedelta(hours=1))
    try:
        with engine.begin() as connection:
            apply_migrations(connection, Path(__file__).parents[2] / "migrations")
            version, identities = _seed(connection, request)
            manifest_before = connection.execute(text("SELECT manifest_hash,manifest FROM market_data_coverage_manifests WHERE dataset_version_id=:id"), {"id": version}).one()
            assert manifest_before.manifest["windows"][0]["start"] == request.start.isoformat()
            assert manifest_before.manifest["windows"][0]["end"] == request.end.isoformat()

        def recover():
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout='15s'"))
                ingest_market_data_recovery_windows(_Provider(), SqlAlchemyMarketBarSink(connection),
                    SqlAlchemyMarketDataRecoveryAuthorizer(connection), version, (request,), identity_map=identities)

        if scenario == "concurrent":
            barrier = Barrier(2)
            def worker():
                barrier.wait(timeout=5)
                recover()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker) for _ in range(2)]
                for future in futures:
                    future.result(timeout=30)
        else:
            recover()
        # A new connection retries exactly the same evidence after prior commit.
        recover()
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT event_time FROM market_bars WHERE dataset_version_id=:id ORDER BY event_time"), {"id": version}).scalars().all()
            expected = [request.start.astimezone(UTC) + i * timedelta(hours=1) for i in range(expected_count)]
            assert [value.astimezone(UTC) for value in rows] == expected
            assert connection.execute(text("SELECT immutable,vintage_label FROM dataset_versions WHERE dataset_version_id=:id"), {"id": version}).one() == (False, "staging")
        if scenario == "crash":
            connection = engine.connect()
            transaction = connection.begin()
            pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            try:
                SqlAlchemyMarketDataVersionFinalizer(connection).finalize(version, (request,), identity_map=identities)
                with engine.begin() as killer:
                    assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}).scalar_one()
                connection.invalidate()
            finally:
                transaction.rollback()
                connection.close()
            with engine.connect() as connection:
                assert connection.execute(text("SELECT immutable,vintage_label FROM dataset_versions WHERE dataset_version_id=:id"), {"id": version}).one() == (False, "staging")
        with engine.begin() as connection:
            ingest_and_seal_market_data_windows(_Provider(), SqlAlchemyMarketBarSink(connection),
                SqlAlchemyMarketDataVersionFinalizer(connection), version, (request,), identity_map=identities)
        with engine.begin() as connection:
            finalizer = SqlAlchemyMarketDataVersionFinalizer(connection)
            finalizer.finalize(version, (request,), identity_map=identities)
            assert connection.execute(text("SELECT immutable,vintage_label FROM dataset_versions WHERE dataset_version_id=:id"), {"id": version}).one() == (True, "sealed")
            assert connection.execute(text("SELECT count(*) FROM market_bars WHERE dataset_version_id=:id"), {"id": version}).scalar_one() == expected_count
            assert connection.execute(text("SELECT manifest_hash,manifest FROM market_data_coverage_manifests WHERE dataset_version_id=:id"), {"id": version}).one() == manifest_before
    finally:
        engine.dispose()




@pytest.mark.integration
def test_fold_distinct_effective_time_is_not_an_exact_retry():
    from hope.domain.market_data.models import MarketBar
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c timezone=America/New_York"})
    request = MarketDataRequest("TEST", ("ABC",), datetime(2026, 11, 1, 0, tzinfo=NY),
        datetime(2026, 11, 1, 3, tzinfo=NY), timedelta(hours=1))
    try:
        with engine.begin() as connection:
            apply_migrations(connection, Path(__file__).parents[2] / "migrations")
            version, identities = _seed(connection, request)
            bar = MarketBar(instrument_id=str(identities[("TEST", "ABC")]),
                event_time=request.start, available_time=request.start, ingestion_time=request.end,
                effective_time=datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=0),
                open="100", high="101", low="99", close="100", volume="1000")
            SqlAlchemyMarketBarSink(connection).append(version, (bar,))
        with engine.begin() as connection:
            alias = bar.model_copy(update={"effective_time": bar.effective_time.astimezone(UTC)})
            SqlAlchemyMarketBarSink(connection).append(version, (alias,))
            assert connection.execute(text("SELECT count(*) FROM market_bars WHERE dataset_version_id=:id"), {"id": version}).scalar_one() == 1
        with engine.begin() as connection:
            conflicting = bar.model_copy(update={"effective_time": datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=1)})
            with pytest.raises(ValueError, match="MARKET_BAR_IDENTITY_CONFLICT"):
                SqlAlchemyMarketBarSink(connection).append(version, (conflicting,))
            assert connection.execute(text("SELECT count(*) FROM market_bars WHERE dataset_version_id=:id"), {"id": version}).scalar_one() == 1
    finally:
        engine.dispose()
