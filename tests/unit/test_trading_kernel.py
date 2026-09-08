from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
import pytest
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType
from hope.domain.trading.kernel import create_order_intent, materialize_order


def make_signal(signal_type: SignalType = SignalType.ENTRY):
    return Signal(signal_id=uuid4(), instrument_id=uuid4(), strategy_version="s1", decision_time=datetime.now(timezone.utc), signal_type=signal_type, conviction=Decimal("0.8"), inputs_hash="a"*64)


def test_rejected_risk_creates_no_order_intent():
    signal = make_signal()
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.REJECT, reason_code="LIMIT", approved_quantity=0)
    assert create_order_intent(signal, risk, OrderSide.BUY, Environment.PAPER) is None


def test_approved_risk_preserves_signal_identity_and_type():
    signal = make_signal(SignalType.EXIT)
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE, reason_code="OK", approved_quantity=Decimal("10"))
    intent = create_order_intent(signal, risk, OrderSide.SELL, Environment.PAPER)
    assert intent is not None
    assert intent.signal_id == signal.signal_id
    assert intent.instrument_id == signal.instrument_id
    assert intent.signal_type is SignalType.EXIT


def test_risk_for_different_signal_is_rejected():
    signal = make_signal()
    risk = RiskAssessment(signal_id=uuid4(), decision=RiskDecision.APPROVE, reason_code="OK", approved_quantity=Decimal("10"))
    with pytest.raises(ValueError, match="RISK_SIGNAL_MISMATCH"):
        create_order_intent(signal, risk, OrderSide.BUY, Environment.PAPER)


def test_order_materialization_is_paper_scoped():
    signal = make_signal()
    risk = RiskAssessment(signal_id=signal.signal_id, decision=RiskDecision.APPROVE, reason_code="OK", approved_quantity=Decimal("10"))
    intent = create_order_intent(signal, risk, OrderSide.BUY, Environment.PAPER)
    order = materialize_order(intent, uuid4())
    assert order.environment is Environment.PAPER
    assert order.instrument_id == signal.instrument_id
