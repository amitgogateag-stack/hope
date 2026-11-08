"""Synthetic provider evidence: immutable snapshots and exact closed-window coverage."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from types import SimpleNamespace
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest

from hope.infrastructure.market_data import provider as p
from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.pipeline import ingest_market_data_window

START=datetime(2026,11,2,13,30,tzinfo=timezone.utc)


def request():
    return p.MarketDataRequest('TEST',('ABC',),START,START+timedelta(minutes=2),timedelta(minutes=1))


def bar(symbol,event,end):
    return RawMarketBar('TEST',symbol,event,event,end,'100','101','99','100','1000')


def complete(asked):
    return [bar(symbol,event,asked.end) for symbol,event in asked.expected_keys]


def test_caller_owned_list_cannot_turn_complete_window_into_persistable_partial_window():
    asked=request();bars=complete(asked)
    batch=p.ProviderMarketDataBatch('TEST',asked,bars,asked.end)
    bars.pop()
    assert isinstance(batch.bars,tuple) and len(batch.bars)==2
    sink=Mock()
    result=ingest_market_data_window(SimpleNamespace(source='TEST',fetch_bars=lambda value:batch),sink,uuid4(),asked,
                                    identity_map={('TEST','ABC'):uuid4()})
    assert result.input_count==asked.expected_count==2 and result.safe_to_persist
    assert len(sink.append.call_args.args[1])==2


@pytest.mark.parametrize('fault',['missing','duplicate','unrequested','offgrid','outside','future_fetch','invalid_type'])
def test_forged_or_modified_adapter_batch_is_revalidated_before_sink(fault):
    asked=request();bars=complete(asked);batch=p.ProviderMarketDataBatch('TEST',asked,bars,asked.end)
    if fault=='missing':object.__setattr__(batch,'bars',batch.bars[:1])
    elif fault=='duplicate':object.__setattr__(batch,'bars',(bars[0],bars[0]))
    elif fault=='unrequested':object.__setattr__(batch,'bars',(bar('OTHER',START,asked.end),bars[1]))
    elif fault=='offgrid':object.__setattr__(batch,'bars',(bar('ABC',START+timedelta(seconds=1),asked.end),bars[1]))
    elif fault=='outside':object.__setattr__(batch,'bars',(bar('ABC',asked.end,asked.end),bars[1]))
    elif fault=='future_fetch':object.__setattr__(batch,'fetched_at',asked.end-timedelta(seconds=1))
    else:batch=SimpleNamespace(source='TEST',request=asked,bars=bars,fetched_at=asked.end)
    sink=Mock()
    with pytest.raises((ValueError,TypeError)):
        ingest_market_data_window(SimpleNamespace(source='TEST',fetch_bars=lambda value:batch),sink,uuid4(),asked,
                                  identity_map={('TEST','ABC'):uuid4()})
    sink.append.assert_not_called()


def test_batch_captures_request_and_forwarded_request_cannot_redefine_expected_window():
    asked=request();batch=p.ProviderMarketDataBatch('TEST',asked,complete(asked),asked.end)
    object.__setattr__(asked,'end',asked.end+timedelta(minutes=1))
    assert batch.request.end==START+timedelta(minutes=2)
    asked=request();sink=Mock()
    def mutate(outbound):
        assert outbound is not asked
        object.__setattr__(outbound,'end',outbound.end+timedelta(minutes=1))
        return p.ProviderMarketDataBatch('TEST',outbound,complete(outbound),outbound.end)
    with pytest.raises(ValueError,match='REQUEST_ECHO_MISMATCH'):
        ingest_market_data_window(SimpleNamespace(source='TEST',fetch_bars=mutate),sink,uuid4(),asked,
                                  identity_map={('TEST','ABC'):uuid4()})
    assert asked.end==START+timedelta(minutes=2)
    sink.append.assert_not_called()


@pytest.mark.parametrize('fault',['generator','bar_type','request_type'])
def test_response_container_and_domain_types_fail_before_consuming_unbounded_iterables(fault):
    asked=request();bars=complete(asked)
    if fault=='generator':
        def infinite():
            pytest.fail('unbounded iterator consumed')
            yield bars[0]
        bars=infinite()
    elif fault=='bar_type':bars=[object()]
    else:asked=object()
    with pytest.raises(TypeError):p.ProviderMarketDataBatch('TEST',asked,bars,START+timedelta(minutes=2))


def test_incomplete_huge_window_is_rejected_without_materializing_expected_grid(monkeypatch):
    asked=p.MarketDataRequest('TEST',('ABC',),datetime.min.replace(tzinfo=timezone.utc),
                             datetime.max.replace(tzinfo=timezone.utc),timedelta(microseconds=1))
    assert asked.expected_count>10**17
    monkeypatch.setattr(p.MarketDataRequest,'expected_keys',property(lambda self:pytest.fail('expected grid materialized')))
    with pytest.raises(ValueError,match='RESPONSE_INCOMPLETE'):
        p.ProviderMarketDataBatch('TEST',asked,(),asked.end)
    small=request()
    payload=[bar('ABC',START+timedelta(minutes=index),small.end) for index in range(2)]
    assert len(p.ProviderMarketDataBatch('TEST',small,payload,small.end).bars)==2


SUBSETS=[indices for count in range(5) for indices in combinations(range(4),count)]


@pytest.mark.parametrize('indices',SUBSETS)
def test_grid_cardinality_proof_preserves_exact_symbol_and_time_completeness(indices):
    asked=p.MarketDataRequest('TEST',('ABC','XYZ'),START,START+timedelta(minutes=2),timedelta(minutes=1))
    bars=complete(asked);chosen=tuple(bars[index] for index in indices)
    if len(indices)==4:
        value=p.ProviderMarketDataBatch('TEST',asked,tuple(reversed(chosen)),asked.end)
        assert value.request.expected_count==4
    else:
        with pytest.raises(ValueError,match='RESPONSE_INCOMPLETE'):p.ProviderMarketDataBatch('TEST',asked,chosen,asked.end)


@pytest.mark.parametrize('market',['INDIA','USA_DST_TRANSITION'])
def test_aware_market_windows_preserve_utc_grid_and_normalized_evidence(market):
    if market=='INDIA':
        start=datetime(2026,11,2,9,15,tzinfo=timezone(timedelta(hours=5,minutes=30)))
        end=start+timedelta(minutes=2);count=2
    else:
        start=datetime(2026,11,1,1,59,tzinfo=timezone(timedelta(hours=-4)))
        end=datetime(2026,11,1,1,2,tzinfo=timezone(timedelta(hours=-5)));count=3
    asked=p.MarketDataRequest('TEST',('ABC',),start,end,timedelta(minutes=1))
    batch=p.ProviderMarketDataBatch('TEST',asked,complete(asked),end)
    result=p.fetch_and_normalize_bars(SimpleNamespace(source='TEST',fetch_bars=lambda value:batch),asked,
                                      identity_map={('TEST','ABC'):uuid4()})
    assert asked.expected_count==len(asked.expected_keys)==result.input_count==count
    assert result.safe_to_persist


def test_concurrent_provider_cache_reload_cannot_change_validated_response():
    asked=request();bars=complete(asked);constructed,changed=Event(),Event()
    def reload():
        assert constructed.wait(5)
        bars.clear();changed.set()
    def fetch(value):
        batch=p.ProviderMarketDataBatch('TEST',value,bars,value.end)
        constructed.set();assert changed.wait(5)
        return batch
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(reload)
        result=p.fetch_and_normalize_bars(SimpleNamespace(source='TEST',fetch_bars=fetch),asked,
                                          identity_map={('TEST','ABC'):uuid4()})
        future.result(timeout=5)
    assert not bars and result.input_count==2 and result.safe_to_persist
