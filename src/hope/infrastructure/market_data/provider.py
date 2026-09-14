from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Protocol, runtime_checkable
from uuid import UUID

from hope.infrastructure.market_data.ingestion import (
    NormalizedMarketBarBatch,
    RawMarketBar,
    normalize_source_bars,
)


@dataclass(frozen=True)
class MarketDataRequest:
    """Deterministic provider request for one closed event-time window."""

    source: str
    source_symbols: tuple[str, ...]
    start: datetime
    end: datetime
    interval: timedelta

    def __post_init__(self) -> None:
        if not self.source or self.source != self.source.strip():
            raise ValueError("MARKET_DATA_SOURCE_NOT_CANONICAL")
        if not self.source_symbols:
            raise ValueError("MARKET_DATA_SYMBOLS_REQUIRED")
        if any(
            not symbol or symbol != symbol.strip()
            for symbol in self.source_symbols
        ):
            raise ValueError("MARKET_DATA_SYMBOL_NOT_CANONICAL")
        if self.source_symbols != tuple(sorted(set(self.source_symbols))):
            raise ValueError("MARKET_DATA_SYMBOLS_MUST_BE_SORTED_UNIQUE")
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.start, self.end)
        ):
            raise ValueError("MARKET_DATA_WINDOW_MUST_BE_TIMEZONE_AWARE")
        if self.end <= self.start:
            raise ValueError("MARKET_DATA_WINDOW_INVALID")
        if self.interval <= timedelta(0):
            raise ValueError("MARKET_DATA_INTERVAL_MUST_BE_POSITIVE")
        if (self.end - self.start) % self.interval != timedelta(0):
            raise ValueError("MARKET_DATA_WINDOW_NOT_ALIGNED_TO_INTERVAL")

    @property
    def expected_keys(self) -> tuple[tuple[str, datetime], ...]:
        """Exact source-symbol/event-time grid required for a complete response."""

        keys: list[tuple[str, datetime]] = []
        event_time = self.start
        while event_time < self.end:
            keys.extend((symbol, event_time) for symbol in self.source_symbols)
            event_time += self.interval
        return tuple(keys)


@dataclass(frozen=True)
class ProviderMarketDataBatch:
    source: str
    request: MarketDataRequest
    bars: tuple[RawMarketBar, ...]
    fetched_at: datetime

    def __post_init__(self) -> None:
        if self.source != self.request.source:
            raise ValueError("PROVIDER_BATCH_SOURCE_MISMATCH")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("PROVIDER_FETCH_TIME_MUST_BE_TIMEZONE_AWARE")
        if self.fetched_at < self.request.end:
            raise ValueError("PROVIDER_FETCH_PRECEDES_CLOSED_WINDOW")
        if any(bar.source != self.source for bar in self.bars):
            raise ValueError("PROVIDER_BAR_SOURCE_MISMATCH")
        requested_symbols = set(self.request.source_symbols)
        if any(bar.source_symbol not in requested_symbols for bar in self.bars):
            raise ValueError("PROVIDER_RETURNED_UNREQUESTED_SYMBOL")
        if any(
            bar.event_time.tzinfo is None or bar.event_time.utcoffset() is None
            for bar in self.bars
        ):
            raise ValueError("PROVIDER_BAR_EVENT_TIME_MUST_BE_TIMEZONE_AWARE")
        if any(
            bar.event_time < self.request.start or bar.event_time >= self.request.end
            for bar in self.bars
        ):
            raise ValueError("PROVIDER_RETURNED_BAR_OUTSIDE_REQUEST_WINDOW")
        if any(
            (bar.event_time - self.request.start) % self.request.interval
            != timedelta(0)
            for bar in self.bars
        ):
            raise ValueError("PROVIDER_RETURNED_BAR_OFF_INTERVAL_GRID")

        actual_keys = tuple((bar.source_symbol, bar.event_time) for bar in self.bars)
        if len(set(actual_keys)) != len(actual_keys):
            raise ValueError("PROVIDER_RETURNED_DUPLICATE_BAR_KEY")
        if set(actual_keys) != set(self.request.expected_keys):
            raise ValueError("PROVIDER_RESPONSE_INCOMPLETE")


@runtime_checkable
class MarketDataProvider(Protocol):
    @property
    def source(self) -> str:
        ...

    def fetch_bars(self, request: MarketDataRequest) -> ProviderMarketDataBatch:
        ...


def fetch_and_normalize_bars(
    provider: MarketDataProvider,
    request: MarketDataRequest,
    *,
    identity_map: Mapping[tuple[str, str], UUID],
) -> NormalizedMarketBarBatch:
    """Fetch through a provider boundary and normalize only coherent evidence."""

    if provider.source != request.source:
        raise ValueError("PROVIDER_REQUEST_SOURCE_MISMATCH")
    batch = provider.fetch_bars(request)
    if batch.request != request:
        raise ValueError("PROVIDER_REQUEST_ECHO_MISMATCH")
    if batch.source != provider.source:
        raise ValueError("PROVIDER_RESPONSE_SOURCE_MISMATCH")
    return normalize_source_bars(batch.bars, identity_map=identity_map)
