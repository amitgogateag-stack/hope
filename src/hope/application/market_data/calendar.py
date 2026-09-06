from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class MarketSessionCalendar:
    """Explicit UTC trading sessions used to distinguish expected session gaps.

    Sessions are half-open intervals ``[open, close)`` and must be supplied in
    chronological order. The calendar is intentionally data-driven so exchange-
    specific holidays and session hours can be supplied without embedding an
    exchange assumption in the quality engine.
    """

    sessions: tuple[tuple[datetime, datetime], ...]

    def __post_init__(self) -> None:
        previous_close: datetime | None = None
        normalized: list[tuple[datetime, datetime]] = []
        for session_open, session_close in self.sessions:
            start = _aware(session_open)
            end = _aware(session_close)
            if end <= start:
                raise ValueError("SESSION_CLOSE_MUST_FOLLOW_OPEN")
            if previous_close is not None and start < previous_close:
                raise ValueError("SESSIONS_MUST_BE_NON_DECREASING")
            normalized.append((start, end))
            previous_close = end
        object.__setattr__(self, "sessions", tuple(normalized))

    def contains(self, event_time: datetime) -> bool:
        value = _aware(event_time)
        return any(start <= value < end for start, end in self.sessions)

    def expected_intermediate_times(
        self,
        previous: datetime,
        current: datetime,
        interval: timedelta,
    ) -> tuple[datetime, ...]:
        """Return expected bar timestamps strictly between two observations."""
        previous = _aware(previous)
        current = _aware(current)
        if interval <= timedelta(0):
            raise ValueError("EXPECTED_INTERVAL_MUST_BE_POSITIVE")
        if current <= previous:
            return ()

        expected: list[datetime] = []
        cursor = previous + interval
        while cursor < current:
            if self.contains(cursor):
                expected.append(cursor)
            cursor += interval
        return tuple(expected)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("HOPE timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
