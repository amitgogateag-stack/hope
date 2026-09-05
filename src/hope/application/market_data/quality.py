from datetime import datetime, timezone
from decimal import Decimal

from hope.domain.market_data.models import DataQualityState, MarketBar


def validate_bar(bar: MarketBar, *, expected_latest_event_time: datetime | None = None) -> DataQualityState:
    if any(value <= Decimal("0") for value in (bar.open, bar.high, bar.low, bar.close)):
        return DataQualityState.MALFORMED
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close) or bar.high < bar.low:
        return DataQualityState.INVALID_OHLC
    if bar.volume < 0:
        return DataQualityState.INVALID_VOLUME
    if expected_latest_event_time is not None:
        expected_latest_event_time = _aware(expected_latest_event_time)
        if _aware(bar.event_time) > expected_latest_event_time:
            return DataQualityState.FUTURE
        if _aware(bar.event_time) < expected_latest_event_time:
            return DataQualityState.STALE
    return DataQualityState.VALID


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("HOPE timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
