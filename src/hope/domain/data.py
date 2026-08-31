from dataclasses import dataclass
from datetime import datetime
from math import isfinite


class DataQualityError(ValueError):
    """Raised when market data violates a hard correctness rule."""


@dataclass(frozen=True, slots=True)
class MarketBar:
    symbol: str
    event_time: datetime
    available_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def validate(self, decision_time: datetime) -> None:
        if self.event_time.tzinfo is None or self.available_time.tzinfo is None:
            raise DataQualityError("bar timestamps must be timezone-aware")
        if decision_time.tzinfo is None:
            raise DataQualityError("decision_time must be timezone-aware")
        if self.event_time > decision_time:
            raise DataQualityError("future event cannot enter evaluation")
        if self.available_time > decision_time:
            raise DataQualityError("future/unavailable bar cannot enter evaluation")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if not all(isfinite(value) for value in values):
            raise DataQualityError("OHLCV values must be finite")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise DataQualityError("OHLC prices must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise DataQualityError("high is inconsistent with OHLC")
        if self.low > min(self.open, self.close, self.high):
            raise DataQualityError("low is inconsistent with OHLC")
        if self.volume < 0:
            raise DataQualityError("volume cannot be negative")
