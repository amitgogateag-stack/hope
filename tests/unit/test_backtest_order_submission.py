from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.audit.models import AuditEventType
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.execution.timeline import ExecutionTimeline, ExecutionTimelineError
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"


def make_bar(at: datetime) -> MarketBar:
    return MarketBar(
        instrument_id=INSTRUMENT,
        event_time=at,
        available_time=at,
        ingestion_time=at,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )


def make_signal(at: datetime) -> Signal:
    return Signal(
        signal_id=UUID("12345678-1234-1234-1234-123456789abc"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=at,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="c" * 64,
    )


def test_execution_timeline_separates_order_submission_from_execution_latency():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    timeline = ExecutionTimeline.from_decision(
        decision,
        order_submission_delay=timedelta(minutes=1),
        latency=timedelta(minutes=2),
    )

    assert timeline.order_time == decision + timedelta(minutes=1)
    assert timeline.fill_eligible_time == decision + timedelta(minutes=3)


def test_backtest_models_order_submission_delay_before_execution_latency():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    too_early = decision + timedelta(minutes=2)
    eligible = decision + timedelta(minutes=3)
    signal = make_signal(decision)
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    result = DeterministicBacktest(
        Decimal("10000"),
        CostModel(version="test"),
        order_submission_delay=timedelta(minutes=1),
        execution_latency=timedelta(minutes=2),
    ).run(
        (make_bar(decision), make_bar(too_early), make_bar(eligible)),
        lambda context: signal if context.as_of == decision else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert len(result.events) == 1
    assert result.events[0].bar.event_time == eligible
    assert result.events[0].result.fill is not None
    assert result.events[0].result.fill.fill_time == eligible
    order_event = next(
        event
        for event in result.events[0].result.audit_events
        if event.event_type is AuditEventType.ORDER_CREATED
    )
    assert order_event.event_time == decision + timedelta(minutes=1)
    assert result.unfilled_order_ids == ()


def test_negative_order_submission_delay_is_rejected():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)

    with pytest.raises(
        ExecutionTimelineError,
        match="ORDER_SUBMISSION_DELAY_MUST_BE_NON_NEGATIVE",
    ):
        ExecutionTimeline.from_decision(
            decision,
            order_submission_delay=timedelta(microseconds=-1),
            latency=timedelta(0),
        )

    with pytest.raises(ValueError, match="ORDER_SUBMISSION_DELAY_MUST_BE_NON_NEGATIVE"):
        DeterministicBacktest(
            Decimal("10000"),
            CostModel(version="test"),
            order_submission_delay=timedelta(microseconds=-1),
        )
