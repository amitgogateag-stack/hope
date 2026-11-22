"""Synthetic window -> real disposable PostgreSQL, interruption and exact retry."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.market_data.pipeline import ingest_market_data_window
from hope.infrastructure.market_data.provider import MarketDataRequest, ProviderMarketDataBatch
from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from test_market_data_recovery_postgres import _seed_staging_version


@pytest.fixture(scope='module')
def database():
    url=os.getenv('HOPE_DATABASE_URL')
    if not url:pytest.skip('Disposable PostgreSQL 16/17 required')
    engine=create_engine(url)
    try:
        with engine.begin() as connection:apply_migrations(connection,Path(__file__).parents[2]/'migrations')
        yield engine
    finally:engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize('fault',['mutable_list','forged_missing','request_echo','fetch_time'])
@pytest.mark.parametrize('interrupt',[False,True])
def test_window_integrity_precedes_persistence_and_survives_rollback_retry(database,fault,interrupt):
    with database.connect() as connection,connection.begin() as outer:
        instrument,version=uuid4(),uuid4()
        start=datetime(2026,11,2,13,30,tzinfo=timezone.utc);end=start+timedelta(minutes=2)
        asked=MarketDataRequest('TEST',('ABC',),start,end,timedelta(minutes=1))
        _seed_staging_version(connection,instrument_id=instrument,dataset_id=uuid4(),dataset_version_id=version,
                              universe_id=uuid4(),universe_version_id=uuid4(),valid_from=start,valid_to=end)
        bars=[RawMarketBar('TEST','ABC',event,event,end,'100','101','99','100','1000')
              for _,event in asked.expected_keys]
        batch=ProviderMarketDataBatch('TEST',asked,bars,end)
        if fault=='mutable_list':bars.pop()
        elif fault=='forged_missing':object.__setattr__(batch,'bars',batch.bars[:1])
        elif fault=='request_echo':
            other=MarketDataRequest('TEST',('ABC',),start+timedelta(minutes=1),end,timedelta(minutes=1))
            batch=ProviderMarketDataBatch('TEST',other,(bars[1],),end)
        else:object.__setattr__(batch,'fetched_at',start)
        provider=SimpleNamespace(source='TEST',fetch_bars=lambda value:batch)
        def invoke():return ingest_market_data_window(provider,SqlAlchemyMarketBarSink(connection),version,asked,
                                                       identity_map={('TEST','ABC'):instrument})
        def count():return connection.execute(text('SELECT count(*) FROM market_bars WHERE dataset_version_id=:v'),{'v':version}).scalar_one()
        if fault!='mutable_list':
            with pytest.raises(ValueError):invoke()
            assert count()==0
            batch=ProviderMarketDataBatch('TEST',asked,bars,end)
        if interrupt:
            with pytest.raises(KeyboardInterrupt):
                with connection.begin_nested():
                    assert invoke().input_count==2
                    raise KeyboardInterrupt()
            assert count()==0
        assert invoke().input_count==2
        assert invoke().input_count==2
        assert count()==2
        assert connection.execute(text('SELECT immutable FROM dataset_versions WHERE dataset_version_id=:v'),{'v':version}).scalar_one() is False
        outer.rollback()  # All test data/effects are disposable; no real acceptance.



@pytest.mark.integration
@pytest.mark.parametrize('target',['bar','instrument','dataset'])
def test_late_transport_alias_mutation_cannot_redirect_real_persistence(database,target):
    from uuid import UUID
    with database.connect() as connection,connection.begin() as outer:
        instrument,version=uuid4(),uuid4();expected_instrument=UUID(int=instrument.int);expected_version=UUID(int=version.int)
        start=datetime(2026,11,2,13,30,tzinfo=timezone.utc);end=start+timedelta(minutes=2)
        asked=MarketDataRequest('TEST',('ABC',),start,end,timedelta(minutes=1))
        _seed_staging_version(connection,instrument_id=instrument,dataset_id=uuid4(),dataset_version_id=version,
                              universe_id=uuid4(),universe_version_id=uuid4(),valid_from=start,valid_to=end)
        bars=[RawMarketBar('TEST','ABC',event,event,end,'100','101','99','100','1000') for _,event in asked.expected_keys]
        batch=ProviderMarketDataBatch('TEST',asked,bars,end)
        class Provider:
            calls=0
            @property
            def source(self):
                self.calls+=1
                if self.calls==2:
                    if target=='bar':
                        for key in ('event_time','available_time','ingestion_time'):object.__setattr__(batch.bars[0],key,end)
                    else:object.__setattr__(instrument if target=='instrument' else version,'int',uuid4().int)
                return 'TEST'
            def fetch_bars(self,value):return batch
        assert ingest_market_data_window(Provider(),SqlAlchemyMarketBarSink(connection),version,asked,identity_map={('TEST','ABC'):instrument}).input_count==2
        rows=connection.execute(text('SELECT instrument_id,event_time FROM market_bars WHERE dataset_version_id=:id ORDER BY event_time'),{'id':expected_version}).all()
        assert rows==[(expected_instrument,start),(expected_instrument,start+timedelta(minutes=1))]
        assert connection.execute(text('SELECT immutable FROM dataset_versions WHERE dataset_version_id=:id'),{'id':expected_version}).scalar_one() is False
        outer.rollback()
