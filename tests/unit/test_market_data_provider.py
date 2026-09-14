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
END = START + timedelta(hours=1)


def request() -> MarketDataRequest:
    return MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=START,
        end=END,
        interval=timedelta(minutes=1),
    )


def bar(*, source: str = "TEST", symbol: str = "ABC") -> RawMarketBar:
    return RawMarketBar(
        source=source,
        source_symbol=symbol,
        event_time=START,
        available_time=START,
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


def test_provider_batch_rejects_unrequested_symbol() -> None:
    with pytest.raises(ValueError, match="UNREQUESTED_SYMBOL"):
        ProviderMarketDataBatch(
            source="TEST",
            request=request(),
            bars=(bar(symbol="OTHER"),),
            fetched_at=END,
        )


def test_provider_batch_rejects_out_of_window_bar() -> None:
    invalid = bar()
    invalid = RawMarketBar(
        **{**invalid.__dict__, "event_time": END}
    )

    with pytest.raises(ValueError, match="OUTSIDE_REQUEST_WINDOW"):
        ProviderMarketDataBatch(
            source="TEST",
            request=request(),
            bars=(invalid,),
            fetched_at=END,
        )


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
            bars=(bar(),),
            fetched_at=END,
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
