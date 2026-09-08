from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.audit.models import AuditEventType
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"
SIGNAL_ID = UUID("88888888-8888-8888-8888-888888888888")


def make_bar(at: datetime, close: str = "100") -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        instrument_id=INSTRUMENT,
        event_time=at,
        available_time=at,
        ingestion_time=at,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("1000"),
    )


def make_signal(at: datetime) -> Signal:
    return Signal(
        signal_id=SIGNAL_ID,
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=at,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="6" * 64,
    )


def test_backtest_retains_risk_rejected_signal_in_decision_ledger():
    decision_time = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = make_signal(decision_time)
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.REJECT,
        reason_code="TEST_REJECTED",
        approved_quantity=Decimal("0"),
    )

    result = DeterministicBacktest(Decimal("10000"), CostModel(version="test")).run(
        (make_bar(decision_time),),
        lambda context: signal if context.as_of == decision_time else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert result.events == ()
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.decision_time == decision_time
    assert decision.bar.instrument_id == INSTRUMENT
    assert decision.signal == signal
    assert decision.risk == assessment
    assert decision.result.intent is None
    assert decision.result.order_id is None
    assert [event.event_type for event in decision.result.audit_events] == [
        AuditEventType.SIGNAL_ACCEPTED,
        AuditEventType.RISK_REJECTED,
    ]
    assert decision.valuation.cash == Decimal("10000")


def test_backtest_keeps_decision_and_fill_event_as_separate_evidence():
    decision_time = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    fill_time = decision_time + timedelta(minutes=1)
    signal = make_signal(decision_time)
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    result = DeterministicBacktest(Decimal("10000"), CostModel(version="test")).run(
        (make_bar(decision_time), make_bar(fill_time, "101")),
        lambda context: signal if context.as_of == decision_time else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert len(result.decisions) == 1
    assert result.decisions[0].result.intent is not None
    assert result.decisions[0].result.fill is None
    assert result.decisions[0].valuation.cash == Decimal("10000")

    assert len(result.events) == 1
    assert result.events[0].result.fill is not None
    assert result.events[0].result.fill.signal_id == signal.signal_id
    assert result.events[0].valuation.cash == Decimal("9899")
