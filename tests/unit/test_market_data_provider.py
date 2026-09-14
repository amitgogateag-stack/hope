from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.provider import (
    MarketDataRequest,
    ProviderMarketDataBatch,
    fetch_and_normalize_bars,
)


START = datetime(2026, 9, 14, 13, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=1)


def request() -> MarketDataRequest:
    return MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=START,
        end=END,
        interval=timedelta(minutes=1),
    )


def bar(
    *,
    source: str = "TEST",
    symbol: str = "ABC",
    event_time: datetime = START,
) -> RawMarketBar:
    return RawMarketBar(
        source=source,
        source_symbol=symbol,
        event_time=event_time,
        available_time=event_time,
        ingestion_time=END,
        open="100",
        high="101",
        low="99",
        close="100",
        volume="1000",
    )


class StubProvider:
    source = "TEST"

    def __init__(self, batch: ProviderMarketDataBatch) -> None:
        self.batch = batch
        self.calls: list[MarketDataRequest] = []

    def fetch_bars(self, value: MarketDataRequest) -> ProviderMarketDataBatch:
        self.calls.append(value)
        return self.batch


def test_request_requires_sorted_unique_symbols() -> None:
    with pytest.raises(ValueError, match="SORTED_UNIQUE"):
        MarketDataRequest(
            source="TEST",
            source_symbols=("BBB", "AAA", "AAA"),
            start=START,
            end=END,
            interval=timedelta(minutes=1),
        )


def test_request_rejects_naive_window() -> None:
    with pytest.raises(ValueError, match="TIMEZONE_AWARE"):
        MarketDataRequest(
            source="TEST",
            source_symbols=("ABC",),
            start=START.replace(tzinfo=None),
            end=END,
            interval=timedelta(minutes=1),
        )


def test_request_requires_window_aligned_to_interval() -> None:
    with pytest.raises(ValueError, match="WINDOW_NOT_ALIGNED_TO_INTERVAL"):
        MarketDataRequest(
            source="TEST",
            source_symbols=("ABC",),
            start=START,
            end=START + timedelta(seconds=90),
            interval=timedelta(minutes=1),
        )


def test_request_expected_keys_are_deterministic_symbol_time_grid() -> None:
    asked = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC", "XYZ"),
        start=START,
        end=START + timedelta(minutes=2),
        interval=timedelta(minutes=1),
    )

    assert asked.expected_keys == (
        ("ABC", START),
        ("XYZ", START),
        ("ABC", START + timedelta(minutes=1)),
        ("XYZ", START + timedelta(minutes=1)),
    )


def test_provider_batch_rejects_unrequested_symbol() -> None:
    with pytest.raises(ValueError, match="UNREQUESTED_SYMBOL"):
        ProviderMarketDataBatch(
            source="TEST",
            request=request(),
            bars=(bar(symbol="OTHER"),),
            fetched_at=END,
        )


def test_provider_batch_rejects_out_of_window_bar() -> None:
    with pytest.raises(ValueError, match="OUTSIDE_REQUEST_WINDOW"):
        ProviderMarketDataBatch(
            source="TEST",
            request=request(),
            bars=(bar(event_time=END),),
            fetched_at=END,
        )


def test_provider_batch_requires_completed_closed_window() -> None:
    with pytest.raises(ValueError, match="FETCH_PRECEDES_CLOSED_WINDOW"):
        ProviderMarketDataBatch(
            source="TEST",
            request=request(),
            bars=(bar(),),
            fetched_at=END - timedelta(microseconds=1),
        )


def test_provider_batch_rejects_naive_bar_event_time() -> None:
    with pytest.raises(ValueError, match="BAR_EVENT_TIME_MUST_BE_TIMEZONE_AWARE"):
        ProviderMarketDataBatch(
            source="TEST",
            request=request(),
            bars=(bar(event_time=START.replace(tzinfo=None)),),
            fetched_at=END,
        )


def test_provider_batch_rejects_bar_off_interval_grid() -> None:
    with pytest.raises(ValueError, match="BAR_OFF_INTERVAL_GRID"):
        ProviderMarketDataBatch(
            source="TEST",
            request=request(),
            bars=(bar(event_time=START + timedelta(seconds=30)),),
            fetched_at=END,
        )


def test_provider_batch_rejects_duplicate_source_bar_key() -> None:
    with pytest.raises(ValueError, match="DUPLICATE_BAR_KEY"):
        ProviderMarketDataBatch(
            source="TEST",
            request=request(),
            bars=(bar(), bar()),
            fetched_at=END,
        )


def test_provider_batch_rejects_partial_response() -> None:
    asked = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC", "XYZ"),
        start=START,
        end=START + timedelta(minutes=2),
        interval=timedelta(minutes=1),
    )
    complete_except_one = (
        bar(symbol="ABC", event_time=START),
        bar(symbol="XYZ", event_time=START),
        bar(symbol="ABC", event_time=START + timedelta(minutes=1)),
    )

    with pytest.raises(ValueError, match="RESPONSE_INCOMPLETE"):
        ProviderMarketDataBatch(
            source="TEST",
            request=asked,
            bars=complete_except_one,
            fetched_at=asked.end,
        )


def test_provider_batch_accepts_complete_multi_symbol_grid() -> None:
    asked = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC", "XYZ"),
        start=START,
        end=START + timedelta(minutes=2),
        interval=timedelta(minutes=1),
    )
    bars = tuple(
        bar(symbol=symbol, event_time=event_time)
        for symbol, event_time in asked.expected_keys
    )

    batch = ProviderMarketDataBatch(
        source="TEST",
        request=asked,
        bars=bars,
        fetched_at=asked.end,
    )

    assert tuple((item.source_symbol, item.event_time) for item in batch.bars) == asked.expected_keys


def test_fetch_fails_closed_on_provider_request_mismatch() -> None:
    asked = request()
    different = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=START,
        end=END + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    provider = StubProvider(
        ProviderMarketDataBatch(
            source="TEST",
            request=different,
            bars=(bar(), bar(event_time=START + timedelta(minutes=1))),
            fetched_at=different.end,
        )
    )

    with pytest.raises(ValueError, match="REQUEST_ECHO_MISMATCH"):
        fetch_and_normalize_bars(
            provider,
            asked,
            identity_map={("TEST", "ABC"): uuid4()},
        )


def test_fetch_and_normalize_returns_canonical_bar() -> None:
    asked = request()
    provider = StubProvider(
        ProviderMarketDataBatch(
            source="TEST",
            request=asked,
            bars=(bar(),),
            fetched_at=END,
        )
    )
    instrument_id = uuid4()

    result = fetch_and_normalize_bars(
        provider,
        asked,
        identity_map={("TEST", "ABC"): instrument_id},
    )

    assert provider.calls == [asked]
    assert result.safe_to_persist
    assert result.bars[0].instrument_id == str(instrument_id)
