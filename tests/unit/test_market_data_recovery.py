from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from hope.domain.market_data.models import MarketBar
from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.provider import MarketDataRequest, ProviderMarketDataBatch
from hope.infrastructure.market_data.recovery import ingest_market_data_recovery_windows


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


class RecordingAuthorizer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def preflight_subset(self, dataset_version_id, requests, *, identity_map) -> None:
        self.calls.append((dataset_version_id, requests, dict(identity_map)))
        if self.error is not None:
            raise self.error


def _window(start: datetime) -> tuple[MarketDataRequest, ProviderMarketDataBatch]:
    end = start + timedelta(minutes=1)
    request = MarketDataRequest(
        source="TEST", source_symbols=("ABC",), start=start, end=end,
        interval=timedelta(minutes=1),
    )
    raw = RawMarketBar(
        source="TEST", source_symbol="ABC", event_time=start,
        available_time=start, ingestion_time=end, open=Decimal("100"),
        high=Decimal("101"), low=Decimal("99"), close=Decimal("100"),
        volume=Decimal("1000"),
    )
    return request, ProviderMarketDataBatch(
        source="TEST", request=request, bars=(raw,), fetched_at=end
    )


def test_recovery_ingests_authorized_subset_without_finalization() -> None:
    request, batch = _window(datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc))
    provider, sink, authorizer = SequencedProvider((batch,)), RecordingSink(), RecordingAuthorizer()
    dataset_version_id, instrument_id = uuid4(), uuid4()
    identity_map = {("TEST", "ABC"): instrument_id}

    result = ingest_market_data_recovery_windows(
        provider, sink, authorizer, dataset_version_id, (request,), identity_map=identity_map
    )

    assert len(result) == 1
    assert provider.calls == [request]
    assert len(sink.calls) == 1
    assert authorizer.calls == [(dataset_version_id, (request,), identity_map)]


def test_recovery_preflight_failure_has_zero_provider_side_effects() -> None:
    request, batch = _window(datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc))
    provider, sink = SequencedProvider((batch,)), RecordingSink()
    authorizer = RecordingAuthorizer(
        ValueError("MARKET_DATA_RECOVERY_REQUEST_OUTSIDE_DECLARED_MANIFEST")
    )
    with pytest.raises(ValueError, match="OUTSIDE_DECLARED_MANIFEST"):
        ingest_market_data_recovery_windows(
            provider, sink, authorizer, uuid4(), (request,),
            identity_map={("TEST", "ABC"): uuid4()},
        )
    assert provider.calls == [] and sink.calls == [] and len(authorizer.calls) == 1


def test_recovery_rejects_identity_errors_before_preflight() -> None:
    request, batch = _window(datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc))
    provider, sink, authorizer = SequencedProvider((batch,)), RecordingSink(), RecordingAuthorizer()
    with pytest.raises(ValueError, match="IDENTITY_MAP_INCOMPLETE"):
        ingest_market_data_recovery_windows(
            provider, sink, authorizer, uuid4(), (request,), identity_map={}
        )
    assert provider.calls == [] and sink.calls == [] and authorizer.calls == []


def test_recovery_partial_provider_failure_never_implies_sealing() -> None:
    first_request, first_batch = _window(datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc))
    second_request, _ = _window(first_request.end)
    provider = SequencedProvider((first_batch, RuntimeError("provider failed")))
    sink, authorizer = RecordingSink(), RecordingAuthorizer()
    with pytest.raises(RuntimeError, match="provider failed"):
        ingest_market_data_recovery_windows(
            provider, sink, authorizer, uuid4(), (first_request, second_request),
            identity_map={("TEST", "ABC"): uuid4()},
        )
    assert provider.calls == [first_request, second_request]
    assert len(sink.calls) == 1
    assert len(authorizer.calls) == 1


def test_recovery_retry_reauthorizes_same_window() -> None:
    request, first_batch = _window(datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc))
    _, retry_batch = _window(request.start)
    provider, sink, authorizer = SequencedProvider((first_batch, retry_batch)), RecordingSink(), RecordingAuthorizer()
    dataset_version_id = uuid4()
    identity_map = {("TEST", "ABC"): uuid4()}

    ingest_market_data_recovery_windows(
        provider, sink, authorizer, dataset_version_id, (request,), identity_map=identity_map
    )
    ingest_market_data_recovery_windows(
        provider, sink, authorizer, dataset_version_id, (request,), identity_map=identity_map
    )

    assert provider.calls == [request, request]
    assert len(authorizer.calls) == 2
    assert len(sink.calls) == 2
