"""Real manifest preflight, concurrent map drift, rollback and exact retry."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine,text

from test_market_data_recovery_postgres import _seed_staging_version
from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.provider import MarketDataRequest,ProviderMarketDataBatch
from hope.infrastructure.market_data.recovery import ingest_market_data_recovery_windows
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from hope.infrastructure.repositories.market_data_manifest import SqlAlchemyMarketDataCoverageManifestRepository
from hope.infrastructure.repositories.market_data_recovery import SqlAlchemyMarketDataRecoveryAuthorizer
from hope.infrastructure.repositories.market_data_finalizer import SqlAlchemyMarketDataVersionFinalizer


@pytest.fixture(scope="module")
def database():
    url=os.getenv("HOPE_DATABASE_URL")
    if not url:pytest.skip("HOPE_DATABASE_URL is not configured")
    engine=create_engine(url)
    try:
        with engine.begin() as connection:apply_migrations(connection,Path(__file__).parents[2]/"migrations")
        yield engine
    finally:engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("entry",["recovery","lifecycle"])
@pytest.mark.parametrize("mutation",["callback","concurrent"])
@pytest.mark.parametrize("interrupt",[False,True])
def test_manifest_identity_is_stable_through_drift_interruption_and_retry(database,entry,mutation,interrupt):
    with database.connect() as connection,connection.begin() as outer:
        a,b,version,universe=uuid4(),uuid4(),uuid4(),uuid4()
        start=datetime(2026,9,15,14,30,tzinfo=timezone.utc)
        _seed_staging_version(connection,instrument_id=a,dataset_id=uuid4(),dataset_version_id=version,
            universe_id=uuid4(),universe_version_id=universe,valid_from=start,valid_to=start+timedelta(minutes=2))
        connection.execute(text("INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) "
            "VALUES(:id,:symbol,'TEST','ACTIVE')"),{"id":b,"symbol":"IDENTITY-"+str(b)})
        connection.execute(text("INSERT INTO universe_members(universe_version_id,instrument_id,valid_from,valid_to) "
            "VALUES(:u,:id,:start,:end)"),{"u":universe,"id":b,"start":start,"end":start+timedelta(minutes=2)})
        connection.execute(text("UPDATE universe_versions SET declared_member_count=2 WHERE universe_version_id=:u"),{"u":universe})
        full=tuple(MarketDataRequest(source="TEST",source_symbols=("ABC","XYZ"),start=start+timedelta(minutes=i),
            end=start+timedelta(minutes=i+1),interval=timedelta(minutes=1)) for i in range(2))
        original={("TEST","ABC"):a,("TEST","XYZ"):b};bindings=dict(original)
        SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(version,universe,full,identity_map=bindings)
        before=connection.execute(text("SELECT manifest_hash FROM market_data_coverage_manifests WHERE dataset_version_id=:v"),{"v":version}).scalar_one()
        requests=full if entry=="lifecycle" else tuple(MarketDataRequest(source="TEST",source_symbols=("ABC",),
            start=r.start,end=r.end,interval=r.interval) for r in full)
        def drift():bindings.update({("TEST","ABC"):b,("TEST","XYZ"):a})
        operation=ingest_market_data_recovery_windows if entry=="recovery" else ingest_and_seal_market_data_windows
        authority=(SqlAlchemyMarketDataRecoveryAuthorizer(connection) if entry=="recovery"
            else SqlAlchemyMarketDataVersionFinalizer(connection))
        def invoke():
            fetching,changed=Event(),Event()
            def reload():
                assert fetching.wait(5);drift();changed.set()
            class Provider:
                source="TEST"
                def fetch_bars(self,request):
                    if mutation=="concurrent":
                        fetching.set();assert changed.wait(5)
                    else:drift()
                    bars=tuple(RawMarketBar(source="TEST",source_symbol=symbol,event_time=t,available_time=t,
                        ingestion_time=request.end,open=str(100 if symbol=="ABC" else 200),
                        high=str(101 if symbol=="ABC" else 201),low=str(99 if symbol=="ABC" else 199),
                        close=str(100 if symbol=="ABC" else 200),volume="1000") for symbol,t in request.expected_keys)
                    return ProviderMarketDataBatch(source="TEST",request=request,bars=bars,fetched_at=request.end)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(reload) if mutation=="concurrent" else None
                result=operation(Provider(),SqlAlchemyMarketBarSink(connection),authority,version,requests,identity_map=bindings)
                if future:future.result(timeout=5)
                return result
        if interrupt:
            with pytest.raises(KeyboardInterrupt):
                with connection.begin_nested():
                    invoke();raise KeyboardInterrupt()
            assert connection.execute(text("SELECT count(*) FROM market_bars WHERE dataset_version_id=:v"),{"v":version}).scalar_one()==0
            assert connection.execute(text("SELECT immutable FROM dataset_versions WHERE dataset_version_id=:v"),{"v":version}).scalar_one() is False
            bindings.clear();bindings.update(original)
        invoke()
        if entry=="recovery":
            bindings.clear();bindings.update(original);invoke()  # Exact retry, no duplicate rows.
        rows=connection.execute(text("SELECT instrument_id,close FROM market_bars WHERE dataset_version_id=:v"),{"v":version}).all()
        assert len(rows)==(2 if entry=="recovery" else 4)
        assert all(price==(100 if instrument==a else 200) for instrument,price in rows)
        assert {instrument for instrument,_ in rows}==({a} if entry=="recovery" else {a,b})
        assert connection.execute(text("SELECT immutable FROM dataset_versions WHERE dataset_version_id=:v"),{"v":version}).scalar_one()==(entry=="lifecycle")
        assert connection.execute(text("SELECT manifest_hash FROM market_data_coverage_manifests WHERE dataset_version_id=:v"),{"v":version}).scalar_one()==before
        outer.rollback()  # Synthetic evidence never survives this test.
