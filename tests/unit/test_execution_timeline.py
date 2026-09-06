from datetime import datetime, timedelta, timezone

import pytest

from hope.domain.execution.timeline import ExecutionTimeline, ExecutionTimelineError


def t(minute: int) -> datetime:
    return datetime(2026, 1, 1, 14, minute, tzinfo=timezone.utc)


def test_latency_shifts_fill_eligibility():
    timeline = ExecutionTimeline.from_decision(t(0), latency=timedelta(minutes=2))
    assert timeline.order_time == t(0)
    assert timeline.fill_eligible_time == t(2)


def test_order_cannot_precede_decision():
    with pytest.raises(ExecutionTimelineError, match="ORDER_TIME_PRECEDES_DECISION_TIME"):
        ExecutionTimeline(t(2), t(1), timedelta(0), t(1))


def test_fill_cannot_precede_eligibility():
    timeline = ExecutionTimeline.from_decision(t(0), latency=timedelta(minutes=2))
    with pytest.raises(ExecutionTimelineError, match="FILL_TIME_PRECEDES_ELIGIBILITY"):
        timeline.with_fill_time(t(1))


def test_quote_before_eligibility_is_rejected():
    timeline = ExecutionTimeline.from_decision(t(0), latency=timedelta(minutes=2))
    with pytest.raises(ExecutionTimelineError, match="QUOTE_PRECEDES_FILL_ELIGIBILITY"):
        timeline.assert_quote_eligible(t(1))


def test_quote_at_eligibility_is_allowed():
    timeline = ExecutionTimeline.from_decision(t(0), latency=timedelta(minutes=2))
    timeline.assert_quote_eligible(t(2))


def test_negative_latency_is_rejected():
    with pytest.raises(ExecutionTimelineError, match="LATENCY_MUST_BE_NON_NEGATIVE"):
        ExecutionTimeline.from_decision(t(0), latency=timedelta(seconds=-1))
