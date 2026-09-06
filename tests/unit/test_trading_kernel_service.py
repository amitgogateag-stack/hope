from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.trading import TradingKernel
from hope.domain.audit import AuditEventType
from hope.domain.execution import CostModel, Environment, ExecutionQuote, OrderSide
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio import PortfolioLedger
from hope.domain.risk import RiskAssessment, RiskDecision
from hope.domain.signal import Signal, SignalType


def make_signal():
    return Signal(
        signal_id=uuid4(), instrument_id=uuid4(), strategy_version="s1",
        decision_time=datetime(2026, 8, 29, 13, 30, tzinfo=timezone.utc),
        signal_type=SignalType.ENTRY, conviction=Decimal("0.8"), inputs_hash="a" * 64,
    )


def test_end_to_end_paper_kernel_preserves_attribution_and_updates_ledger():
    signal = make_signal()
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                          reason_code="OK", approved_quantity=Decimal("10"))
    instrument = signal.instrument_id
    quote_time = signal.decision_time + timedelta(minutes=1)
    quote = ExecutionQuote(instrument, quote_time, Decimal("99"), Decimal("100"))
    timeline = ExecutionTimeline.from_decision(signal.decision_time, latency=timedelta(minutes=1)).with_fill_time(quote_time)
    kernel = TradingKernel(PortfolioLedger(Decimal("10000")))

    result = kernel.process(signal, risk, OrderSide.BUY, Environment.PAPER, quote,
                            CostModel("v1", Decimal("0.001"), Decimal("10")),
                            timeline=timeline)

    assert result.fill is not None
    assert result.fill.signal_id == signal.signal_id
    assert result.fill.instrument_id == instrument
    assert result.portfolio_state.positions[instrument].quantity == Decimal("10")
    assert [e.event_type for e in result.audit_events] == [
        AuditEventType.SIGNAL_ACCEPTED,
        AuditEventType.RISK_APPROVED,
        AuditEventType.ORDER_CREATED,
        AuditEventType.FILL_CREATED,
        AuditEventType.PORTFOLIO_UPDATED,
    ]
    assert all(len(e.payload_hash) == 64 for e in result.audit_events)


def test_rejected_risk_is_audited_and_has_no_order_or_fill():
    signal = make_signal()
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.REJECT,
                          reason_code="LIMIT", approved_quantity=Decimal("0"))
    kernel = TradingKernel(PortfolioLedger(Decimal("10000")))

    result = kernel.process(signal, risk, OrderSide.BUY, Environment.PAPER, None, None)

    assert result.intent is None
    assert result.order_id is None
    assert result.fill is None
    assert result.portfolio_state.cash == Decimal("10000")
    assert result.audit_events[-1].event_type is AuditEventType.RISK_REJECTED


def test_paper_execution_requires_market_quote_and_cost_model():
    signal = make_signal()
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                          reason_code="OK", approved_quantity=Decimal("1"))
    kernel = TradingKernel(PortfolioLedger(Decimal("10000")))

    with pytest.raises(ValueError, match="PAPER_EXECUTION_REQUIRES_QUOTE_AND_COST_MODEL"):
        kernel.process(signal, risk, OrderSide.BUY, Environment.PAPER, None, None)


def test_kernel_rejects_quote_before_signal_decision_time():
    signal = make_signal()
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                          reason_code="OK", approved_quantity=Decimal("1"))
    quote = ExecutionQuote(signal.instrument_id, signal.decision_time.replace(minute=29),
                           Decimal("99"), Decimal("100"))
    kernel = TradingKernel(PortfolioLedger(Decimal("10000")))
    with pytest.raises(ValueError, match="QUOTE_PRECEDES_SIGNAL_DECISION_TIME"):
        kernel.process(signal, risk, OrderSide.BUY, Environment.PAPER, quote,
                       CostModel("v1", Decimal("0.001"), Decimal("10")),
                       timeline=ExecutionTimeline.from_decision(signal.decision_time, latency=timedelta(0)))


def test_kernel_requires_timeline_for_quote_backed_execution():
    signal = make_signal()
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE,
                          reason_code="OK", approved_quantity=Decimal("1"))
    quote = ExecutionQuote(signal.instrument_id, signal.decision_time,
                           Decimal("99"), Decimal("100"))
    kernel = TradingKernel(PortfolioLedger(Decimal("10000")))

    with pytest.raises(ValueError, match="QUOTE_EXECUTION_REQUIRES_TIMELINE"):
        kernel.process(signal, risk, OrderSide.BUY, Environment.PAPER, quote,
                       CostModel("v1", Decimal("0.001"), Decimal("10")))
