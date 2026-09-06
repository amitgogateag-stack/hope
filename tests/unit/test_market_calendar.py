from datetime import datetime, timedelta, timezone

import pytest

from hope.application.market_data.calendar import MarketSessionCalendar


def dt(hour: int, minute: int = 0, day: int = 28) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def test_calendar_allows_overnight_gap_between_sessions():
    calendar = MarketSessionCalendar(
        sessions=(
            (dt(10), dt(16)),
            (dt(10, day=29), dt(16, day=29)),
        )
    )
    assert calendar.expected_intermediate_times(dt(15, 59), dt(10, day=29), timedelta(minutes=1)) == ()


def test_calendar_detects_missing_intraday_timestamp():
    calendar = MarketSessionCalendar(sessions=((dt(10), dt(16)),))
    assert calendar.expected_intermediate_times(dt(10), dt(10, 2), timedelta(minutes=1)) == (dt(10, 1),)


def test_calendar_rejects_overlapping_sessions():
    with pytest.raises(ValueError, match="SESSIONS_MUST_BE_NON_DECREASING"):
        MarketSessionCalendar(sessions=((dt(10), dt(12)), (dt(11), dt(13))))


def test_calendar_rejects_naive_session_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketSessionCalendar(sessions=((datetime(2026, 8, 28, 10), dt(12)),))


def test_calendar_rejects_non_positive_interval():
    calendar = MarketSessionCalendar(sessions=((dt(10), dt(16)),))
    with pytest.raises(ValueError, match="EXPECTED_INTERVAL_MUST_BE_POSITIVE"):
        calendar.expected_intermediate_times(dt(10), dt(11), timedelta(0))
