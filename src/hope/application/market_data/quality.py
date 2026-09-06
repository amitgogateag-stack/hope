from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hope.domain.market_data.models import DataQualityState, MarketBar


@dataclass(frozen=True)
class DataQualityReport:
    """Deterministic batch quality result used as a research safety gate."""

    states: tuple[DataQualityState, ...]
    invalid_indices: tuple[int, ...]
    missing_instrument_ids: tuple[str, ...]
    duplicate_keys: tuple[tuple[str, datetime], ...]
    gap_keys: tuple[tuple[str, datetime, datetime], ...]
    ordering_violations: tuple[int, ...]
    empty_input: bool

    @property
    def safe(self) -> bool:
        return (
            not self.empty_input
            and not self.invalid_indices
            and not self.missing_instrument_ids
            and not self.duplicate_keys
            and not self.gap_keys
            and not self.ordering_violations
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
    expected_interval: timedelta | None = None,
) -> DataQualityReport:
    """Validate a deterministic batch and expose unsafe evidence explicitly.

    Duplicate identity is the same (instrument_id, event_time) key. Missing is
    asserted only when an expected instrument set is supplied. If an expected
    interval is supplied, every consecutive bar for an instrument must advance
    by exactly that interval; a mismatch is classified as incomplete data.
    """
    if expected_latest_event_time is not None:
        expected_latest_event_time = _aware(expected_latest_event_time)
    if expected_interval is not None and expected_interval <= timedelta(0):
        raise ValueError("EXPECTED_INTERVAL_MUST_BE_POSITIVE")

    states: list[DataQualityState] = []
    seen: set[tuple[str, datetime]] = set()
    duplicate_keys: set[tuple[str, datetime]] = set()
    gap_keys: set[tuple[str, datetime, datetime]] = set()
    ordering_violations: list[int] = []
    last_by_instrument: dict[str, datetime] = {}

    for index, bar in enumerate(bars):
        event_time = _aware(bar.event_time)
        key = (bar.instrument_id, event_time)
        if key in seen:
            duplicate_keys.add(key)
            states.append(DataQualityState.DUPLICATE)
            continue
        seen.add(key)

        previous = last_by_instrument.get(bar.instrument_id)
        if previous is not None:
            delta = event_time - previous
            if delta <= timedelta(0):
                ordering_violations.append(index)
            elif expected_interval is not None and delta != expected_interval:
                gap_keys.add((bar.instrument_id, previous, event_time))
        last_by_instrument[bar.instrument_id] = event_time
        states.append(validate_bar(bar, expected_latest_event_time=expected_latest_event_time))

    present = {bar.instrument_id for bar in bars}
    missing = tuple(sorted(set(expected_instrument_ids or ()) - present))
    if missing:
        states.extend(DataQualityState.MISSING for _ in missing)

    invalid = tuple(i for i, state in enumerate(states[:len(bars)]) if state is not DataQualityState.VALID)
    if gap_keys:
        # A gap is a dataset-level incompleteness, even though the bars around it
        # may individually be valid. Keep their per-bar states intact and expose
        # the halt condition through report.safe.
        pass

    return DataQualityReport(
        states=tuple(states),
        invalid_indices=invalid,
        missing_instrument_ids=missing,
        duplicate_keys=tuple(sorted(duplicate_keys)),
        gap_keys=tuple(sorted(gap_keys)),
        ordering_violations=tuple(ordering_violations),
        empty_input=not bars,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("HOPE timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
