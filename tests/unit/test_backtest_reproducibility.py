from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from hope.application.backtests.engine import DeterministicBacktest
from hope.application.trading.service import TradingKernel
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.portfolio.ledger import PortfolioLedger
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
        inputs_hash="d" * 64,
    )


def run_approved_backtest():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = make_signal(decision)
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )
    return DeterministicBacktest(
        Decimal("10000"), CostModel(version="test")
    ).run(
        (make_bar(decision), make_bar(decision + timedelta(minutes=1))),
        lambda context: signal if context.as_of == decision else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )


def test_backtest_audit_evidence_is_reproducible_for_filled_order():
    first = run_approved_backtest()
    second = run_approved_backtest()

    assert len(first.events) == len(second.events) == 1
    assert first.events[0].result.audit_events == second.events[0].result.audit_events


def test_risk_rejection_audit_evidence_is_reproducible():
    decision = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = make_signal(decision)
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.REJECT,
        reason_code="TEST_REJECTED",
        approved_quantity=Decimal("0"),
    )

    first = TradingKernel(PortfolioLedger(Decimal("10000"))).process(
        signal,
        assessment,
        OrderSide.BUY,
        Environment.BACKTEST,
        None,
        None,
    )
    second = TradingKernel(PortfolioLedger(Decimal("10000"))).process(
        signal,
        assessment,
        OrderSide.BUY,
        Environment.BACKTEST,
        None,
        None,
    )

    assert first.audit_events == second.audit_events
