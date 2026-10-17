"""Explicit test calendars verify UTC scheduling, not exchange holiday coverage."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.strategy.candidates import StrategyMarket
from hope.infrastructure.scheduling.paper import (
    OperationalPaperJobBinding, OperationalPaperSchedule, build_operational_paper_runs,
)


@pytest.mark.parametrize("dates,expected_us", [
    (("2026-03-06", "2026-03-09"), (14, 13)),
    (("2026-10-30", "2026-11-02"), (13, 14)),
])
def test_india_and_usa_remain_independent_across_us_dst(dates, expected_us):
    bindings, schedules, calendars = [], [], {}
    for market, zone, opening, closing in (
        (StrategyMarket.USA, "America/New_York", "09:30", "16:00"),
        (StrategyMarket.INDIA, "Asia/Kolkata", "09:15", "15:30"),
    ):
        binding = OperationalPaperJobBinding(uuid4(), market, "certified", lambda _: None)
        bindings.append(binding)
        schedules.append(OperationalPaperSchedule(binding.strategy_version_id, market, "certified", timedelta(0)))
        calendars[market] = MarketSessionCalendar(tuple(
            (datetime.fromisoformat(f"{day}T{opening}").replace(tzinfo=ZoneInfo(zone)),
             datetime.fromisoformat(f"{day}T{closing}").replace(tzinfo=ZoneInfo(zone))) for day in dates))
    start = datetime.fromisoformat(dates[0]).replace(tzinfo=UTC)
    end = datetime.fromisoformat(dates[-1]).replace(tzinfo=UTC) + timedelta(days=1)
    runs = build_operational_paper_runs(bindings, schedules, calendars, start=start, end=end)
    usa = [r.scheduled_for for r in runs if r.job_key.startswith("paper:USA:")]
    india = [r.scheduled_for for r in runs if r.job_key.startswith("paper:INDIA:")]
    assert [(t.hour, t.minute) for t in usa] == [(h, 30) for h in expected_us]
    assert [(t.hour, t.minute) for t in india] == [(3, 45), (3, 45)]
    assert build_operational_paper_runs(bindings, schedules, calendars, start=start, end=end) == runs
    # A restart window excludes the already elapsed first session without moving
    # the remaining durable identity. Explicit closed dates produce no runs.
    restart = datetime.fromisoformat(dates[-1]).replace(tzinfo=UTC)
    later = build_operational_paper_runs(bindings, schedules, calendars, start=restart, end=end)
    assert later == tuple(r for r in runs if r.scheduled_for >= restart)
    closed = {market: MarketSessionCalendar(()) for market in calendars}
    assert build_operational_paper_runs(bindings, schedules, closed, start=start, end=end) == ()


def test_session_close_is_exclusive_and_duplicate_schedules_are_rejected():
    version = uuid4()
    binding = OperationalPaperJobBinding(version, StrategyMarket.USA, "close", lambda _: None)
    start = datetime(2026, 10, 16, 13, 30, tzinfo=UTC)
    end = start + timedelta(hours=6, minutes=30)
    calendar = MarketSessionCalendar(((start, end),))
    schedule = OperationalPaperSchedule(version, StrategyMarket.USA, "close", end - start)
    with pytest.raises(ValueError, match="PAPER_SCHEDULE_OFFSET_OUTSIDE_SESSION"):
        build_operational_paper_runs((binding,), (schedule,), {StrategyMarket.USA: calendar}, start=start, end=end)
    schedule = OperationalPaperSchedule(version, StrategyMarket.USA, "close", timedelta(0))
    with pytest.raises(ValueError, match="PAPER_SCHEDULE_DUPLICATE_DEFINITION"):
        build_operational_paper_runs((binding,), (schedule, schedule), {StrategyMarket.USA: calendar}, start=start, end=end)
