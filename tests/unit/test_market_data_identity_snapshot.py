from datetime import datetime, timezone
from uuid import uuid4
from collections.abc import Mapping
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from test_market_data_recovery import _window, RecordingSink, RecordingAuthorizer, SequencedProvider
from hope.infrastructure.market_data.recovery import ingest_market_data_recovery_windows
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import fetch_and_normalize_bars
from hope.infrastructure.market_data.identity import snapshot_request_bindings
from test_market_data_lifecycle import RecordingFinalizer

START=datetime(2026,9,14,14,30,tzinfo=timezone.utc)


def test_provider_cannot_change_identity_after_recovery_preflight():
    request,batch=_window(datetime(2026,9,14,14,30,tzinfo=timezone.utc))
    original,replacement=uuid4(),uuid4();key=("TEST","ABC");bindings={key:original}
    class Provider(SequencedProvider):
        def fetch_bars(self,request):
            bindings[key]=replacement
            return super().fetch_bars(request)
    sink,authorizer=RecordingSink(),RecordingAuthorizer()
    ingest_market_data_recovery_windows(Provider((batch,)),sink,authorizer,uuid4(),(request,),identity_map=bindings)
    assert authorizer.calls[0][2][key]==original
    assert sink.calls[0][1][0].instrument_id==str(original)


@pytest.mark.parametrize("entry",["recovery","lifecycle","provider"])
@pytest.mark.parametrize("change",["replace","delete","clear"])
def test_snapshot_survives_mutation_during_provider_callback(entry,change):
    request,batch=_window(START);key=("TEST","ABC");original=uuid4();bindings={key:original}
    class Provider(SequencedProvider):
        def fetch_bars(self,request):
            if change=="replace":bindings[key]=uuid4()
            elif change=="delete":del bindings[key]
            else:bindings.clear()
            return super().fetch_bars(request)
    provider=Provider((batch,));sink=RecordingSink()
    if entry=="provider":
        batches=(fetch_and_normalize_bars(provider,request,identity_map=bindings),)
    elif entry=="recovery":
        batches=ingest_market_data_recovery_windows(provider,sink,RecordingAuthorizer(),uuid4(),(request,),identity_map=bindings)
    else:
        finalizer=RecordingFinalizer()
        batches=ingest_and_seal_market_data_windows(provider,sink,finalizer,uuid4(),(request,),identity_map=bindings)
        assert finalizer.preflight_calls[0][2]==finalizer.calls[0][2]=={key:original}
    assert batches[0].safe_to_persist and batches[0].bars[0].instrument_id==str(original)


@pytest.mark.parametrize("entry",["recovery","lifecycle"])
def test_one_snapshot_spans_preflight_both_windows_sink_and_finalization(entry):
    first,b1=_window(START);second,b2=_window(first.end)
    key=("TEST","ABC");original=uuid4();bindings={key:original};seen=[]
    class Authority:
        def preflight(self,*args,identity_map):
            seen.append(identity_map);bindings[key]=uuid4()
        preflight_subset=preflight
        def finalize(self,*args,identity_map):seen.append(identity_map)
    class Sink(RecordingSink):
        def append(self,*args):
            bindings.clear();super().append(*args)
    authority=Authority();sink=Sink();provider=SequencedProvider((b1,b2))
    operation=ingest_market_data_recovery_windows if entry=="recovery" else ingest_and_seal_market_data_windows
    result=operation(provider,sink,authority,uuid4(),(first,second),identity_map=bindings)
    assert all(batch.bars[0].instrument_id==str(original) for batch in result)
    assert all(mapping=={key:original} for mapping in seen)
    if entry=="lifecycle":assert seen[0] is seen[1]
    with pytest.raises(TypeError):seen[0][key]=uuid4()


@pytest.mark.parametrize("entry",["recovery","lifecycle"])
def test_authorizer_cannot_mutate_approved_snapshot(entry):
    request,batch=_window(START);key=("TEST","ABC");bindings={key:uuid4()}
    class Authority:
        def preflight(self,*args,identity_map):identity_map[key]=uuid4()
        preflight_subset=preflight
    provider=SequencedProvider((batch,));sink=RecordingSink()
    operation=ingest_market_data_recovery_windows if entry=="recovery" else ingest_and_seal_market_data_windows
    with pytest.raises(TypeError):operation(provider,sink,Authority(),uuid4(),(request,),identity_map=bindings)
    assert provider.calls==[] and sink.calls==[]


def test_concurrent_reload_cannot_change_inflight_authorized_instrument():
    request,batch=_window(START);key=("TEST","ABC");original=uuid4();bindings={key:original}
    fetching,changed=Event(),Event()
    def reload():
        assert fetching.wait(5);bindings[key]=uuid4();changed.set()
    class Provider(SequencedProvider):
        def fetch_bars(self,request):
            fetching.set();assert changed.wait(5);return super().fetch_bars(request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(reload);sink=RecordingSink()
        ingest_market_data_recovery_windows(Provider((batch,)),sink,RecordingAuthorizer(),uuid4(),(request,),identity_map=bindings)
        future.result(timeout=5)
    assert sink.calls[0][1][0].instrument_id==str(original)


def test_scoped_copy_does_not_iterate_unrequested_bindings_or_read_values_twice():
    request,_=_window(START);key=("TEST","ABC");original=uuid4();reads=[]
    class Bindings(Mapping):
        def __iter__(self):raise AssertionError("unbounded full-map enumeration")
        def __len__(self):raise AssertionError("unneeded global cardinality")
        def __getitem__(self,k):
            reads.append(k)
            return original if len(reads)==1 else uuid4()
    snapshot=snapshot_request_bindings((request,request),Bindings())
    assert dict(snapshot)=={key:original} and reads==[key]


def test_snapshot_does_not_invent_defaultdict_binding_for_missing_key():
    request,_=_window(START);factory=[]
    def invent():factory.append(True);return uuid4()
    bindings=defaultdict(invent)
    assert dict(snapshot_request_bindings((request,),bindings))=={}
    assert not bindings and not factory


def test_retry_takes_new_snapshot_and_reauthorizes_changed_bindings():
    request,batch=_window(START);key=("TEST","ABC");first,second=uuid4(),uuid4();bindings={key:first}
    authorizer=RecordingAuthorizer();sink=RecordingSink();provider=SequencedProvider((batch,batch))
    for value in (first,second):
        bindings[key]=value
        ingest_market_data_recovery_windows(provider,sink,authorizer,uuid4(),(request,),identity_map=bindings)
    assert [c[2][key] for c in authorizer.calls]==[first,second]
    assert [c[1][0].instrument_id for c in sink.calls]==[str(first),str(second)]
