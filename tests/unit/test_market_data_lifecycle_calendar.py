from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.market_data.models import MarketBar
from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import MarketDataRequest, ProviderMarketDataBatch


class SequencedProvider:
    source = "TEST"

    def __init__(self, responses: tuple[ProviderMarketDataBatch, ...]) -> None:
        self.responses = list(responses)
        self.calls: list[MarketDataRequest] = []

    def fetch_bars(self, request: MarketDataRequest) -> ProviderMarketDataBatch:
        self.calls.append(request)
        return self.responses.pop(0)


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, tuple[MarketBar, ...]]] = []

    def append(self, dataset_version_id: UUID, bars: tuple[MarketBar, ...]) -> None:
        self.calls.append((dataset_version_id, bars))


class RecordingFinalizer:
    def __init__(self) -> None:
        self.calls = 0

    def finalize(self, dataset_version_id, requests, *, identity_map) -> None:
        self.calls += 1


def _request(start: datetime) -> tuple[MarketDataRequest, ProviderMarketDataBatch]:
    end = start + timedelta(minutes=1)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=end,
        interval=timedelta(minutes=1),
    )
    raw = RawMarketBar(
        source="TEST",
        source_symbol="ABC",
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


def test_lifecycle_calendar_allows_closed_market_gap() -> None:
    first_start = datetime(2026, 9, 14, 15, 59, tzinfo=timezone.utc)
    second_start = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    first_request, first_batch = _request(first_start)
    second_request, second_batch = _request(second_start)
    calendar = MarketSessionCalendar(
        sessions=(
            (datetime(2026, 9, 14, 10, tzinfo=timezone.utc), datetime(2026, 9, 14, 16, tzinfo=timezone.utc)),
            (datetime(2026, 9, 15, 10, tzinfo=timezone.utc), datetime(2026, 9, 15, 16, tzinfo=timezone.utc)),
        )
    )
    provider = SequencedProvider((first_batch, second_batch))
    sink = RecordingSink()
    finalizer = RecordingFinalizer()

    ingest_and_seal_market_data_windows(
        provider,
        sink,
        finalizer,
        uuid4(),
        (first_request, second_request),
        identity_map={("TEST", "ABC"): uuid4()},
        session_calendar=calendar,
    )

    assert provider.calls == [first_request, second_request]
    assert len(sink.calls) == 2
    assert finalizer.calls == 1


def test_lifecycle_calendar_rejects_skipped_trading_slot_before_provider_call() -> None:
    first_start = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    second_start = datetime(2026, 9, 14, 10, 2, tzinfo=timezone.utc)
    first_request, first_batch = _request(first_start)
    second_request, second_batch = _request(second_start)
    calendar = MarketSessionCalendar(
        sessions=((datetime(2026, 9, 14, 10, tzinfo=timezone.utc), datetime(2026, 9, 14, 16, tzinfo=timezone.utc)),)
    )
    provider = SequencedProvider((first_batch, second_batch))

    with pytest.raises(ValueError, match="WINDOWS_SKIP_TRADING_SLOTS"):
        ingest_and_seal_market_data_windows(
            provider,
            RecordingSink(),
            RecordingFinalizer(),
            uuid4(),
            (first_request, second_request),
            identity_map={("TEST", "ABC"): uuid4()},
            session_calendar=calendar,
        )

    assert provider.calls == []


def test_lifecycle_calendar_rejects_off_session_request_before_provider_call() -> None:
    request_start = datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc)
    request, batch = _request(request_start)
    calendar = MarketSessionCalendar(
        sessions=((datetime(2026, 9, 14, 10, tzinfo=timezone.utc), datetime(2026, 9, 14, 16, tzinfo=timezone.utc)),)
    )
    provider = SequencedProvider((batch,))

    with pytest.raises(ValueError, match="WINDOW_OUTSIDE_SESSION"):
        ingest_and_seal_market_data_windows(
            provider,
            RecordingSink(),
            RecordingFinalizer(),
            uuid4(),
            (request,),
            identity_map={("TEST", "ABC"): uuid4()},
            session_calendar=calendar,
        )

    assert provider.calls == []
