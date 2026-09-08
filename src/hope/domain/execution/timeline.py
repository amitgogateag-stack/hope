from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


class ExecutionTimelineError(ValueError):
    pass


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionTimelineError(f"{name}_MUST_BE_TIMEZONE_AWARE")


@dataclass(frozen=True)
class ExecutionTimeline:
    """Immutable timestamps separating decision, order, eligibility and fill.

    The timeline is deliberately independent of market data. A quote may only
    be used for a fill when its timestamp is at or after fill_eligible_time.
    """

    decision_time: datetime
    order_time: datetime
    latency: timedelta
    fill_eligible_time: datetime
    fill_time: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("decision_time", "order_time", "fill_eligible_time"):
            _aware(getattr(self, name), name.upper())
        if self.fill_time is not None:
            _aware(self.fill_time, "FILL_TIME")
        if self.latency < timedelta(0):
            raise ExecutionTimelineError("LATENCY_MUST_BE_NON_NEGATIVE")
        if self.order_time < self.decision_time:
            raise ExecutionTimelineError("ORDER_TIME_PRECEDES_DECISION_TIME")
        expected = self.order_time + self.latency
        if self.fill_eligible_time != expected:
            raise ExecutionTimelineError("FILL_ELIGIBILITY_MUST_EQUAL_ORDER_TIME_PLUS_LATENCY")
        if self.fill_time is not None and self.fill_time < self.fill_eligible_time:
            raise ExecutionTimelineError("FILL_TIME_PRECEDES_ELIGIBILITY")

    @classmethod
    def from_decision(
        cls,
        decision_time: datetime,
        *,
        latency: timedelta,
        order_submission_delay: timedelta = timedelta(0),
    ) -> "ExecutionTimeline":
        _aware(decision_time, "DECISION_TIME")
        if latency < timedelta(0):
            raise ExecutionTimelineError("LATENCY_MUST_BE_NON_NEGATIVE")
        if order_submission_delay < timedelta(0):
            raise ExecutionTimelineError("ORDER_SUBMISSION_DELAY_MUST_BE_NON_NEGATIVE")
        order_time = decision_time + order_submission_delay
        return cls(decision_time, order_time, latency, order_time + latency)

    def with_fill_time(self, fill_time: datetime) -> "ExecutionTimeline":
        return ExecutionTimeline(
            self.decision_time,
            self.order_time,
            self.latency,
            self.fill_eligible_time,
            fill_time,
        )

    def assert_quote_eligible(self, quote_time: datetime) -> None:
        _aware(quote_time, "QUOTE_TIME")
        if quote_time < self.fill_eligible_time:
            raise ExecutionTimelineError("QUOTE_PRECEDES_FILL_ELIGIBILITY")
