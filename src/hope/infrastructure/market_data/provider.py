from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Mapping, Protocol, runtime_checkable
from uuid import UUID
from hope.infrastructure.market_data.identity import snapshot_request_bindings

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
        start, end = self.start.astimezone(UTC), self.end.astimezone(UTC)
        if end <= start:
            raise ValueError("MARKET_DATA_WINDOW_INVALID")
        if self.interval <= timedelta(0):
            raise ValueError("MARKET_DATA_INTERVAL_MUST_BE_POSITIVE")
        if (end - start) % self.interval != timedelta(0):
            raise ValueError("MARKET_DATA_WINDOW_NOT_ALIGNED_TO_INTERVAL")

    @property
    def expected_count(self) -> int:
        """Grid cardinality without materializing an unreceived time window."""
        duration = self.end.astimezone(UTC) - self.start.astimezone(UTC)
        return (duration // self.interval) * len(self.source_symbols)

    @property
    def expected_keys(self) -> tuple[tuple[str, datetime], ...]:
        """Exact source-symbol/event-time grid required for a complete response."""

        keys: list[tuple[str, datetime]] = []
        event_time = self.start.astimezone(UTC)
        end = self.end.astimezone(UTC)
        while event_time < end:
            keys.extend((symbol, event_time) for symbol in self.source_symbols)
            event_time += self.interval
        return tuple(keys)


def _instant(value):
    if type(value) is not datetime:raise TypeError('PROVIDER_TIMESTAMP_TYPE_INVALID')
    return value.replace(tzinfo=None) if value.tzinfo is None or value.utcoffset() is None else value.astimezone(UTC)


def _snapshot_bar(bar):
    if type(bar) is not RawMarketBar:raise TypeError('PROVIDER_BATCH_BAR_TYPE_INVALID')
    if type(bar.source) is not str or type(bar.source_symbol) is not str:
        raise TypeError('PROVIDER_SOURCE_TYPE_INVALID')
    values={name:getattr(bar,name) for name in ('open','high','low','close','volume')}
    # Do not invoke a transport object's __str__/numeric hook after validation.
    if any(type(value) not in {str,int,float,bool,Decimal,type(None)} for value in values.values()):
        raise TypeError('PROVIDER_NUMERIC_PAYLOAD_TYPE_INVALID')
    return RawMarketBar(bar.source,bar.source_symbol,_instant(bar.event_time),_instant(bar.available_time),
                        _instant(bar.ingestion_time),**values,effective_time=None if bar.effective_time is None else _instant(bar.effective_time))


def _snapshot_request(request: MarketDataRequest) -> MarketDataRequest:
    if not isinstance(request, MarketDataRequest):
        raise TypeError("PROVIDER_REQUEST_TYPE_INVALID")
    source,symbols,interval=request.source,request.source_symbols,request.interval
    if type(source) is not str or type(symbols) is not tuple or any(type(symbol) is not str for symbol in symbols) or type(interval) is not timedelta:
        raise TypeError('PROVIDER_REQUEST_VALUE_TYPE_INVALID')
    return MarketDataRequest(source=source, source_symbols=symbols,
                             start=_instant(request.start), end=_instant(request.end), interval=interval)


@dataclass(frozen=True)
class ProviderMarketDataBatch:
    source: str
    request: MarketDataRequest
    bars: tuple[RawMarketBar, ...]
    fetched_at: datetime

    def __post_init__(self) -> None:
        # Frozen dataclasses do not freeze caller-owned containers. Retain one
        # immutable response snapshot before checking completeness; never a
        # list/generator that can change between validation and persistence.
        if type(self.bars) not in {tuple,list}:
            raise TypeError("PROVIDER_BATCH_BAR_CONTAINER_INVALID")
        object.__setattr__(self, "bars", tuple(_snapshot_bar(bar) for bar in self.bars))
        object.__setattr__(self, "request", _snapshot_request(self.request))
        if any(not isinstance(bar, RawMarketBar) for bar in self.bars):
            raise TypeError("PROVIDER_BATCH_BAR_TYPE_INVALID")
        if type(self.source) is not str:raise TypeError('PROVIDER_SOURCE_TYPE_INVALID')
        object.__setattr__(self,'fetched_at',_instant(self.fetched_at))
        if self.source != self.request.source:
            raise ValueError("PROVIDER_BATCH_SOURCE_MISMATCH")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("PROVIDER_FETCH_TIME_MUST_BE_TIMEZONE_AWARE")
        start, end = self.request.start.astimezone(UTC), self.request.end.astimezone(UTC)
        if self.fetched_at.astimezone(UTC) < end:
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
            bar.event_time.astimezone(UTC) < start or bar.event_time.astimezone(UTC) >= end
            for bar in self.bars
        ):
            raise ValueError("PROVIDER_RETURNED_BAR_OUTSIDE_REQUEST_WINDOW")
        if any(
            (bar.event_time.astimezone(UTC) - start) % self.request.interval
            != timedelta(0)
            for bar in self.bars
        ):
            raise ValueError("PROVIDER_RETURNED_BAR_OFF_INTERVAL_GRID")

        actual_keys = tuple((bar.source_symbol, bar.event_time.astimezone(UTC)) for bar in self.bars)
        if len(set(actual_keys)) != len(actual_keys):
            raise ValueError("PROVIDER_RETURNED_DUPLICATE_BAR_KEY")
        # Every unique key is already a requested symbol on the exact closed
        # interval grid. Cardinality therefore proves equality with that grid
        # without allocating all expected keys for a huge sparse response.
        if len(actual_keys) != self.request.expected_count:
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

    request = _snapshot_request(request)
    identity_map = snapshot_request_bindings((request,), identity_map)
    if provider.source != request.source:
        raise ValueError("PROVIDER_REQUEST_SOURCE_MISMATCH")
    # The adapter receives a separate request; mutating its echo must not change
    # the private expected request captured before any provider side effect.
    batch = provider.fetch_bars(_snapshot_request(request))
    if not isinstance(batch, ProviderMarketDataBatch):
        raise TypeError("PROVIDER_RESPONSE_TYPE_INVALID")
    # Revalidate returned evidence, including adapters which bypassed frozen
    # fields or returned an old object changed since its construction.
    batch = ProviderMarketDataBatch(source=batch.source,request=batch.request,
                                    bars=batch.bars,fetched_at=batch.fetched_at)
    if (
        batch.request.source != request.source
        or batch.request.source_symbols != request.source_symbols
        or batch.request.start.astimezone(UTC) != request.start.astimezone(UTC)
        or batch.request.end.astimezone(UTC) != request.end.astimezone(UTC)
        or batch.request.interval != request.interval
    ):
        raise ValueError("PROVIDER_REQUEST_ECHO_MISMATCH")
    if batch.source != provider.source:
        raise ValueError("PROVIDER_RESPONSE_SOURCE_MISMATCH")
    return normalize_source_bars(batch.bars, identity_map=identity_map)
