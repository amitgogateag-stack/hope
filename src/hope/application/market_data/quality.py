from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from hope.domain.market_data.models import DataQualityState, MarketBar


@dataclass(frozen=True)
class DataQualityReport:
    """Deterministic batch quality result used as a research safety gate."""

    states: tuple[DataQualityState, ...]
    invalid_indices: tuple[int, ...]
    missing_instrument_ids: tuple[str, ...]
    duplicate_keys: tuple[tuple[str, datetime], ...]
    empty_input: bool

    @property
    def safe(self) -> bool:
        return (
            not self.empty_input
            and not self.invalid_indices
            and not self.missing_instrument_ids
            and not self.duplicate_keys
        )


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


def validate_bars(
    bars: tuple[MarketBar, ...],
    *,
    expected_latest_event_time: datetime | None = None,
    expected_instrument_ids: tuple[str, ...] | None = None,
) -> DataQualityReport:
    """Validate a deterministic batch and expose unsafe evidence explicitly.

    Duplicate identity is the same (instrument_id, event_time) key. Missing is
    asserted only when an expected instrument set is supplied.
    """
    if expected_latest_event_time is not None:
        expected_latest_event_time = _aware(expected_latest_event_time)

    states: list[DataQualityState] = []
    seen: set[tuple[str, datetime]] = set()
    duplicate_keys: set[tuple[str, datetime]] = set()

    for bar in bars:
        key = (bar.instrument_id, _aware(bar.event_time))
        if key in seen:
            duplicate_keys.add(key)
            states.append(DataQualityState.DUPLICATE)
            continue
        seen.add(key)
        states.append(validate_bar(bar, expected_latest_event_time=expected_latest_event_time))

    present = {bar.instrument_id for bar in bars}
    missing = tuple(sorted(set(expected_instrument_ids or ()) - present))
    if missing:
        states.extend(DataQualityState.MISSING for _ in missing)

    invalid = tuple(i for i, state in enumerate(states[:len(bars)]) if state is not DataQualityState.VALID)
    return DataQualityReport(
        states=tuple(states),
        invalid_indices=invalid,
        missing_instrument_ids=missing,
        duplicate_keys=tuple(sorted(duplicate_keys)),
        empty_input=not bars,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("HOPE timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
