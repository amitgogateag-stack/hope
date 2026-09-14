from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from hope.domain.market_data.models import MarketBar
from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import MarketDataRequest, ProviderMarketDataBatch


class SequencedProvider:
    source = "TEST"

    def __init__(self, responses: tuple[ProviderMarketDataBatch | Exception, ...]) -> None:
        self.responses = list(responses)
        self.calls: list[MarketDataRequest] = []

    def fetch_bars(self, request: MarketDataRequest) -> ProviderMarketDataBatch:
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, tuple[MarketBar, ...]]] = []

    def append(self, dataset_version_id: UUID, bars: tuple[MarketBar, ...]) -> None:
        self.calls.append((dataset_version_id, bars))


class RecordingFinalizer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[
            tuple[UUID, tuple[MarketDataRequest, ...], dict[tuple[str, str], UUID]]
        ] = []

    def finalize(self, dataset_version_id: UUID, requests, *, identity_map) -> None:
        self.calls.append((dataset_version_id, requests, dict(identity_map)))
        if self.error is not None:
            raise self.error


def _window(start: datetime, symbol: str = "ABC") -> tuple[MarketDataRequest, ProviderMarketDataBatch]:
    end = start + timedelta(minutes=1)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=(symbol,),
        start=start,
        end=end,
        interval=timedelta(minutes=1),
    )
    raw = RawMarketBar(
        source="TEST",
        source_symbol=symbol,
        event_time=start,
        available_time=start,
        ingestion_time=end,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )
    return request, ProviderMarketDataBatch(
        source="TEST",
        request=request,
        bars=(raw,),
        fetched_at=end,
    )


def test_lifecycle_ingests_then_finalizes_once() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    first_request, first_batch = _window(start)
    second_request, second_batch = _window(first_request.end)
    provider = SequencedProvider((first_batch, second_batch))
    sink = RecordingSink()
    finalizer = RecordingFinalizer()
    dataset_version_id = uuid4()
    identity_map = {("TEST", "ABC"): uuid4()}

    batches = ingest_and_seal_market_data_windows(
        provider,
        sink,
        finalizer,
        dataset_version_id,
        (first_request, second_request),
        identity_map=identity_map,
    )

    assert provider.calls == [first_request, second_request]
    assert len(batches) == 2
    assert len(sink.calls) == 2
    assert finalizer.calls == [
        (dataset_version_id, (first_request, second_request), identity_map)
    ]


def test_lifecycle_rejects_empty_request_set_before_side_effects() -> None:
    provider = SequencedProvider(())
    sink = RecordingSink()
    finalizer = RecordingFinalizer()

    with pytest.raises(ValueError, match="WINDOWS_REQUIRED"):
        ingest_and_seal_market_data_windows(
            provider, sink, finalizer, uuid4(), (), identity_map={}
        )

    assert provider.calls == []
    assert sink.calls == []
    assert finalizer.calls == []


def test_lifecycle_rejects_noncontiguous_windows_before_side_effects() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    first_request, first_batch = _window(start)
    second_request, second_batch = _window(first_request.end + timedelta(minutes=1))
    provider = SequencedProvider((first_batch, second_batch))
    sink = RecordingSink()
    finalizer = RecordingFinalizer()

    with pytest.raises(ValueError, match="WINDOWS_NOT_CONTIGUOUS"):
        ingest_and_seal_market_data_windows(
            provider,
            sink,
            finalizer,
            uuid4(),
            (first_request, second_request),
            identity_map={("TEST", "ABC"): uuid4()},
        )

    assert provider.calls == []
    assert sink.calls == []
    assert finalizer.calls == []


def test_lifecycle_rejects_identity_map_errors_before_side_effects() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request, batch = _window(start)
    provider = SequencedProvider((batch,))
    finalizer = RecordingFinalizer()

    with pytest.raises(ValueError, match="IDENTITY_MAP_INCOMPLETE"):
        ingest_and_seal_market_data_windows(
            provider,
            RecordingSink(),
            finalizer,
            uuid4(),
            (request,),
            identity_map={},
        )
    assert provider.calls == []
    assert finalizer.calls == []


def test_lifecycle_failure_after_partial_persist_never_finalizes() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    first_request, first_batch = _window(start)
    second_request, _ = _window(first_request.end)
    provider = SequencedProvider((first_batch, RuntimeError("provider failed")))
    sink = RecordingSink()
    finalizer = RecordingFinalizer()

    with pytest.raises(RuntimeError, match="provider failed"):
        ingest_and_seal_market_data_windows(
            provider,
            sink,
            finalizer,
            uuid4(),
            (first_request, second_request),
            identity_map={("TEST", "ABC"): uuid4()},
        )

    assert provider.calls == [first_request, second_request]
    assert len(sink.calls) == 1
    assert finalizer.calls == []


def test_lifecycle_finalizer_failure_propagates_after_persistence() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request, batch = _window(start)
    provider = SequencedProvider((batch,))
    sink = RecordingSink()
    finalizer = RecordingFinalizer(
        ValueError("MARKET_DATA_PERSISTED_COVERAGE_MISSING")
    )

    with pytest.raises(ValueError, match="PERSISTED_COVERAGE_MISSING"):
        ingest_and_seal_market_data_windows(
            provider,
            sink,
            finalizer,
            uuid4(),
            (request,),
            identity_map={("TEST", "ABC"): uuid4()},
        )

    assert len(sink.calls) == 1
    assert len(finalizer.calls) == 1
