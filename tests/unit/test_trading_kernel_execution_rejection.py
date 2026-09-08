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
SIGNAL_ID = UUID("11111111-2222-3333-4444-555555555555")
INSTRUMENT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
ORDER_ID = UUID("99999999-8888-7777-6666-555555555555")
FILL_ID = UUID("12121212-3434-5656-7878-909090909090")
LATE_FILL_ID = UUID("abababab-cdcd-efef-1212-343434343434")


def make_signal(decision: datetime) -> Signal:
    return Signal(
        signal_id=SIGNAL_ID,
        instrument_id=INSTRUMENT_ID,
        strategy_version="test",
        decision_time=decision,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="f" * 64,
    )


def make_risk(signal: Signal) -> RiskAssessment:
    return RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("10"),
    )


def test_execution_rejection_is_terminal_audited_and_portfolio_safe():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = make_signal(decision)
    timeline = ExecutionTimeline.from_decision(
        decision,
        latency=timedelta(0),
        order_submission_delay=timedelta(minutes=1),
    )
    ledger = PortfolioLedger(Decimal("10000"))
    kernel = TradingKernel(ledger)
    submission = kernel.process(
        signal,
        make_risk(signal),
        OrderSide.BUY,
        Environment.BACKTEST,
        None,
        None,
        order_id=ORDER_ID,
        timeline=timeline,
    )
    state_before_rejection = ledger.state
    rejection_time = decision + timedelta(minutes=2)

    rejected = kernel.reject_order(
        submission.intent,
        ORDER_ID,
        decision_time=decision,
        timeline=timeline,
        rejection_time=rejection_time,
        reason_code="NO_EXECUTABLE_LIQUIDITY",
    )

    assert rejected.fill is None
    assert rejected.rejection is not None
    assert rejected.rejection.reason_code == "NO_EXECUTABLE_LIQUIDITY"
    assert rejected.rejection.rejection_time == rejection_time
    assert rejected.portfolio_state == state_before_rejection
    assert ledger.state == state_before_rejection
    assert rejected.audit_events[0].event_type is AuditEventType.EXECUTION_REJECTED
    validate_audit_sequence(submission.audit_events + rejected.audit_events)

    late_fill_time = decision + timedelta(minutes=3)
    with pytest.raises(ValueError, match="TERMINAL_ORDER_CANNOT_FILL"):
        kernel.execute_order(
            submission.intent,
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

    assert ledger.state == state_before_rejection


def test_partially_filled_order_cannot_be_misclassified_as_execution_rejected():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = make_signal(decision)
    timeline = ExecutionTimeline.from_decision(decision, latency=timedelta(0))
    ledger = PortfolioLedger(Decimal("10000"))
    kernel = TradingKernel(ledger)
    submission = kernel.process(
        signal,
        make_risk(signal),
        OrderSide.BUY,
        Environment.BACKTEST,
        None,
        None,
        order_id=ORDER_ID,
        timeline=timeline,
    )
    fill_time = decision + timedelta(minutes=1)
    kernel.execute_order(
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

    with pytest.raises(ValueError, match="ORDER_NOT_REJECTABLE"):
        kernel.reject_order(
            submission.intent,
            ORDER_ID,
            decision_time=decision,
            timeline=timeline,
            rejection_time=decision + timedelta(minutes=2),
            reason_code="NO_EXECUTABLE_LIQUIDITY",
        )

    assert ledger.state.positions[INSTRUMENT_ID].quantity == Decimal("4")
    assert ledger.state.cash == Decimal("9600")
