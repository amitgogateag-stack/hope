from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataQualityState(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    STALE = "STALE"
    FUTURE = "FUTURE"
    DUPLICATE = "DUPLICATE"
    MALFORMED = "MALFORMED"
    INCOMPLETE_SESSION = "INCOMPLETE_SESSION"
    INVALID_OHLC = "INVALID_OHLC"
    INVALID_VOLUME = "INVALID_VOLUME"
    AMBIGUOUS_IDENTITY = "AMBIGUOUS_IDENTITY"


class MarketBar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instrument_id: str = Field(min_length=1)
    event_time: datetime
    available_time: datetime
    effective_time: datetime | None = None
    ingestion_time: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_temporal_and_ohlc(self):
        timestamps = (self.event_time, self.available_time, self.effective_time, self.ingestion_time)
        if any(t is not None and t.tzinfo is None for t in timestamps):
            raise ValueError("HOPE timestamps must be timezone-aware")
        if self.available_time < self.event_time:
            raise ValueError("available_time cannot precede event_time")
        if self.ingestion_time < self.available_time:
            raise ValueError("ingestion_time cannot precede available_time")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.high < self.low:
            raise ValueError("invalid OHLC relationship")
        return self


class SignalState(StrEnum):
    NO_SIGNAL = "NO_SIGNAL"
    SIGNAL = "SIGNAL"
    STALE_SIGNAL = "STALE_SIGNAL"
