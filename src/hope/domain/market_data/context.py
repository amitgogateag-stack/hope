from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hope.domain.market_data.models import MarketBar


class PITMarketContext(BaseModel):
    """Immutable market information that was available at one decision time.

    A context may contain only bars whose event, availability, ingestion,
    and optional effective timestamps are at or before ``as_of``. Bars are ordered deterministically
    by event time, availability time, ingestion time, and instrument id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: datetime
    bars: tuple[MarketBar, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_point_in_time(self) -> "PITMarketContext":
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("PIT context timestamp must be timezone-aware")
        for bar in self.bars:
            if (
                any(t.astimezone(UTC) > self.as_of.astimezone(UTC)
                    for t in (bar.event_time, bar.available_time, bar.ingestion_time, bar.effective_time)
                    if t is not None)
            ):
                raise ValueError("PIT_CONTEXT_CONTAINS_UNAVAILABLE_INFORMATION")
        keys = tuple(_bar_key(bar) for bar in self.bars)
        if keys != tuple(sorted(keys)):
            raise ValueError("PIT_CONTEXT_BARS_MUST_BE_DETERMINISTICALLY_ORDERED")
        return self

    @property
    def latest_bar(self) -> MarketBar | None:
        return self.bars[-1] if self.bars else None

    def for_instrument(self, instrument_id: str) -> tuple[MarketBar, ...]:
        return tuple(bar for bar in self.bars if bar.instrument_id == instrument_id)


def build_pit_market_context(bars: tuple[MarketBar, ...] | list[MarketBar], as_of: datetime) -> PITMarketContext:
    """Build a PIT-safe context by excluding information unavailable at ``as_of``."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("PIT context timestamp must be timezone-aware")
    visible = tuple(
        bar
        for bar in bars
        if (
            all(t.astimezone(UTC) <= as_of.astimezone(UTC)
                for t in (bar.event_time, bar.available_time, bar.ingestion_time, bar.effective_time)
                if t is not None)
        )
    )
    visible = tuple(
        sorted(
            visible,
            key=_bar_key,
        )
    )
    return PITMarketContext(as_of=as_of, bars=visible)


def _bar_key(bar: MarketBar) -> tuple[datetime, datetime, datetime, str]:
    return (bar.event_time.astimezone(UTC), bar.available_time.astimezone(UTC),
            bar.ingestion_time.astimezone(UTC), bar.instrument_id)
