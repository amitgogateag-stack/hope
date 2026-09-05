from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
import pytest
from hope.application.trading import TradingKernel
from hope.domain.execution import Environment, OrderSide
from hope.domain.portfolio import PortfolioLedger
from hope.domain.risk import RiskAssessment, RiskDecision
from hope.domain.signal import Signal, SignalType


def make_signal():
    decision = datetime(2026, 8, 29, 13, 0, tzinfo=timezone.utc)
    return Signal(signal_id=uuid4(), instrument_id=uuid4(), strategy_version="s1", decision_time=decision,
                  signal_type=SignalType.ENTRY, conviction=Decimal("0.5"), inputs_hash="a" * 64)


def test_kernel_rejected_risk_uses_signal_decision_time_deterministically():
    signal = make_signal()
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.REJECT, reason_code="LIMIT", approved_quantity=Decimal("0"))
    result = TradingKernel(PortfolioLedger(Decimal("100"))).process(
        signal, risk, OrderSide.BUY, Environment.PAPER, None, None)
    assert result.audit_events[-1].event_time == signal.decision_time


def test_kernel_has_no_wall_clock_dependency_for_approved_path():
    signal = make_signal()
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.REJECT, reason_code="LIMIT", approved_quantity=Decimal("0"))
    first = TradingKernel(PortfolioLedger(Decimal("100"))).process(signal, risk, OrderSide.BUY, Environment.PAPER, None, None)
    second = TradingKernel(PortfolioLedger(Decimal("100"))).process(signal, risk, OrderSide.BUY, Environment.PAPER, None, None)
    assert [e.event_time for e in first.audit_events] == [e.event_time for e in second.audit_events]
