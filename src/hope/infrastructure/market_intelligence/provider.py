from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from hope.domain.market_intelligence.models import MarketIntelligenceEvent


@dataclass(frozen=True)
class IntelligenceRequest:
    source: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not self.source or self.source != self.source.strip():
            raise ValueError("INTELLIGENCE_SOURCE_NOT_CANONICAL")
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.start, self.end)
        ):
            raise ValueError("INTELLIGENCE_WINDOW_MUST_BE_TIMEZONE_AWARE")
        if self.end <= self.start:
            raise ValueError("INTELLIGENCE_WINDOW_INVALID")


@dataclass(frozen=True)
class ProviderIntelligenceBatch:
    source: str
    request: IntelligenceRequest
    events: tuple[MarketIntelligenceEvent, ...]
    fetched_at: datetime

    def __post_init__(self) -> None:
        if self.source != self.request.source:
            raise ValueError("INTELLIGENCE_BATCH_SOURCE_MISMATCH")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("INTELLIGENCE_FETCH_TIME_MUST_BE_TIMEZONE_AWARE")
        if any(event.source != self.source for event in self.events):
            raise ValueError("INTELLIGENCE_EVENT_SOURCE_MISMATCH")
        if any(
            event.available_time < self.request.start
            or event.available_time >= self.request.end
            for event in self.events
        ):
            raise ValueError("INTELLIGENCE_EVENT_OUTSIDE_REQUEST_WINDOW")


@runtime_checkable
class MarketIntelligenceProvider(Protocol):
    """Replaceable boundary: API, file, fixture, or future connector may implement it."""

    @property
    def source(self) -> str:
        ...

    def fetch_events(self, request: IntelligenceRequest) -> ProviderIntelligenceBatch:
        ...


def fetch_intelligence_events(
    provider: MarketIntelligenceProvider,
    request: IntelligenceRequest,
) -> tuple[MarketIntelligenceEvent, ...]:
    if provider.source != request.source:
        raise ValueError("INTELLIGENCE_PROVIDER_REQUEST_SOURCE_MISMATCH")
    batch = provider.fetch_events(request)
    if batch.request != request:
        raise ValueError("INTELLIGENCE_PROVIDER_REQUEST_ECHO_MISMATCH")
    if batch.source != provider.source:
        raise ValueError("INTELLIGENCE_PROVIDER_RESPONSE_SOURCE_MISMATCH")
    return batch.events
