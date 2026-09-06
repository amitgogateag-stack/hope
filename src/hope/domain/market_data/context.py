from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hope.domain.market_data.models import MarketBar


class PITMarketContext(BaseModel):
    """Immutable market information that was available at one decision time.

    A context may contain only bars whose event and availability timestamps are
    both at or before ``as_of``. Bars are ordered deterministically by event
    time, availability time, ingestion time, and instrument id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: datetime
    bars: tuple[MarketBar, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_point_in_time(self) -> "PITMarketContext":
        if self.as_of.tzinfo is None:
            raise ValueError("PIT context timestamp must be timezone-aware")
        for bar in self.bars:
            if bar.event_time > self.as_of or bar.available_time > self.as_of:
                raise ValueError("PIT_CONTEXT_CONTAINS_UNAVAILABLE_INFORMATION")
        if tuple(self.bars) != tuple(
            sorted(
                self.bars,
                key=lambda b: (b.event_time, b.available_time, b.ingestion_time, b.instrument_id),
            )
        ):
            raise ValueError("PIT_CONTEXT_BARS_MUST_BE_DETERMINISTICALLY_ORDERED")
        return self

    @property
    def latest_bar(self) -> MarketBar | None:
        return self.bars[-1] if self.bars else None

    def for_instrument(self, instrument_id: str) -> tuple[MarketBar, ...]:
        return tuple(bar for bar in self.bars if bar.instrument_id == instrument_id)


def build_pit_market_context(bars: tuple[MarketBar, ...] | list[MarketBar], as_of: datetime) -> PITMarketContext:
    """Build a PIT-safe context by excluding information unavailable at ``as_of``."""
    if as_of.tzinfo is None:
        raise ValueError("PIT context timestamp must be timezone-aware")
    visible = tuple(
        bar
        for bar in bars
        if bar.event_time <= as_of and bar.available_time <= as_of
    )
    visible = tuple(
        sorted(
            visible,
            key=lambda b: (b.event_time, b.available_time, b.ingestion_time, b.instrument_id),
        )
    )
    return PITMarketContext(as_of=as_of, bars=visible)
