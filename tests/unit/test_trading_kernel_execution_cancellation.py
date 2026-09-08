from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.trading.service import TradingKernel
from hope.domain.audit.models import AuditEventType
from hope.domain.audit.validator import validate_audit_sequence
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.simulator import CostModel, ExecutionQuote
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
SIGNAL_ID = UUID("10101010-2020-3030-4040-505050505050")
INSTRUMENT_ID = UUID("aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb")
ORDER_ID = UUID("60606060-7070-8080-9090-a0a0a0a0a0a0")
FILL_ID = UUID("b0b0b0b0-c0c0-d0d0-e0e0-f0f0f0f0f0f0")
LATE_FILL_ID = UUID("11111111-aaaa-bbbb-cccc-222222222222")


def make_signal(decision: datetime) -> Signal:
    return Signal(
        signal_id=SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        strategy_version="cancellation-test",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="c" * 64,
    )


def make_risk(signal: Signal) -> RiskAssessment:
    return RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("10"),
    )


def submit(kernel: TradingKernel, decision: datetime, timeline: ExecutionTimeline):
    signal = make_signal(decision)
    return kernel.process(
        signal,
        make_risk(signal),
        OrderSide.BUY,
        Environment.BACKTEST,
        None,
        None,
        order_id=ORDER_ID,
        timeline=timeline,
    )


def test_open_order_cancellation_is_terminal_audited_and_reproducible():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    timeline = ExecutionTimeline.from_decision(
        decision,
        latency=timedelta(0),
        order_submission_delay=timedelta(minutes=1),
    )
    cancellation_time = decision + timedelta(minutes=2)

    def run_once():
        ledger = PortfolioLedger(Decimal("10000"))
        kernel = TradingKernel(ledger)
        submission = submit(kernel, decision, timeline)
        state_before = ledger.state
        cancelled = kernel.cancel_order(
            submission.intent,
            ORDER_ID,
            decision_time=decision,
            timeline=timeline,
            cancellation_time=cancellation_time,
            reason_code="OPERATOR_CANCELLED",
        )
        return ledger, kernel, submission, state_before, cancelled

    first_ledger, first_kernel, first_submission, first_before, first = run_once()
    _, _, second_submission, _, second = run_once()

    assert first.fill is None
    assert first.rejection is None
    assert first.cancellation is not None
    assert first.cancellation.reason_code == "OPERATOR_CANCELLED"
    assert first.cancellation.cancellation_time == cancellation_time
    assert first.cancellation.cancelled_quantity == Decimal("10")
    assert first.portfolio_state == first_before
    assert first_ledger.state == first_before
    assert first.audit_events[0].event_type is AuditEventType.ORDER_CANCELLED
    assert first.audit_events == second.audit_events
    assert first_submission.audit_events == second_submission.audit_events
    validate_audit_sequence(first_submission.audit_events + first.audit_events)

    late_fill_time = decision + timedelta(minutes=3)
    with pytest.raises(ValueError, match="TERMINAL_ORDER_CANNOT_FILL"):
        first_kernel.execute_order(
            first_submission.intent,
            ORDER_ID,
            ExecutionQuote(
                INSTRUMENT_ID,
                late_fill_time,
                Decimal("100"),
                Decimal("100"),
            ),
            CostModel(version="test"),
            decision_time=decision,
            fill_id=LATE_FILL_ID,
            timeline=timeline.with_fill_time(late_fill_time),
            quantity=Decimal("1"),
        )

    assert first_ledger.state == first_before


def test_partially_filled_order_cancels_only_remaining_quantity_without_portfolio_mutation():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    timeline = ExecutionTimeline.from_decision(decision, latency=timedelta(0))
    ledger = PortfolioLedger(Decimal("10000"))
    kernel = TradingKernel(ledger)
    submission = submit(kernel, decision, timeline)

    fill_time = decision + timedelta(minutes=1)
    partial = kernel.execute_order(
        submission.intent,
        ORDER_ID,
        ExecutionQuote(
            INSTRUMENT_ID,
            fill_time,
            Decimal("100"),
            Decimal("100"),
        ),
        CostModel(version="test"),
        decision_time=decision,
        fill_id=FILL_ID,
        timeline=timeline.with_fill_time(fill_time),
        quantity=Decimal("4"),
    )
    state_before_cancellation = ledger.state
    cancellation_time = decision + timedelta(minutes=2)

    cancelled = kernel.cancel_order(
        submission.intent,
        ORDER_ID,
        decision_time=decision,
        timeline=timeline,
        cancellation_time=cancellation_time,
        reason_code="REMAINDER_CANCELLED",
    )

    assert cancelled.cancellation is not None
    assert cancelled.cancellation.cancelled_quantity == Decimal("6")
    assert cancelled.portfolio_state == state_before_cancellation
    assert ledger.state.positions[INSTRUMENT_ID].quantity == Decimal("4")
    assert ledger.state.cash == Decimal("9600")
    validate_audit_sequence(
        submission.audit_events + partial.audit_events + cancelled.audit_events
    )

    late_fill_time = decision + timedelta(minutes=3)
    with pytest.raises(ValueError, match="TERMINAL_ORDER_CANNOT_FILL"):
        kernel.execute_order(
            submission.intent,
            ORDER_ID,
            ExecutionQuote(
                INSTRUMENT_ID,
                late_fill_time,
                Decimal("101"),
                Decimal("101"),
            ),
            CostModel(version="test"),
            decision_time=decision,
            fill_id=LATE_FILL_ID,
            timeline=timeline.with_fill_time(late_fill_time),
            quantity=Decimal("1"),
        )

    assert ledger.state == state_before_cancellation
