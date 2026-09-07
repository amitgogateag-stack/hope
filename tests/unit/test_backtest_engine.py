from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hope.application.backtests.engine import BacktestDataQualityError, DeterministicBacktest
from hope.domain.audit.models import AuditEventType
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType
from uuid import UUID


UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"


def bar(at: datetime) -> MarketBar:
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


def backtest() -> DeterministicBacktest:
    return DeterministicBacktest(Decimal("10000"), CostModel(version="test"))


def no_strategy(_context):
    return None


def no_risk(_signal):
    raise AssertionError("risk must not run when data-quality gate fails")


def test_backtest_rejects_empty_market_data_before_strategy_execution():
    with pytest.raises(BacktestDataQualityError) as exc:
        backtest().run([], no_strategy, no_risk, OrderSide.BUY)

    assert exc.value.report.empty_input is True
    assert exc.value.report.safe is False


def test_backtest_rejects_duplicate_market_data_before_strategy_execution():
    timestamp = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    bars = [bar(timestamp), bar(timestamp)]

    with pytest.raises(BacktestDataQualityError) as exc:
        backtest().run(bars, no_strategy, no_risk, OrderSide.BUY)

    assert exc.value.report.duplicate_keys == ((INSTRUMENT, timestamp),)
    assert exc.value.report.safe is False


def test_backtest_accepts_valid_ordered_market_data():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    bars = [bar(first), bar(first + timedelta(minutes=1))]

    result = backtest().run(bars, no_strategy, no_risk, OrderSide.BUY)

    assert len(result.valuations) == 2
    assert result.unfilled_order_ids == ()


def test_backtest_rejects_signal_decision_after_context_availability():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = Signal(
        signal_id=UUID("22222222-2222-2222-2222-222222222222"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first + timedelta(minutes=1),
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="0" * 64,
    )

    def future_decision(_context):
        return signal

    with pytest.raises(ValueError, match="SIGNAL_DECISION_AFTER_CONTEXT"):
        backtest().run((bar(first),), future_decision, no_risk, OrderSide.BUY)


def test_backtest_rejects_signal_decision_before_context_availability():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = Signal(
        signal_id=UUID("44444444-4444-4444-4444-444444444444"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first - timedelta(minutes=1),
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="2" * 64,
    )

    with pytest.raises(ValueError, match="SIGNAL_DECISION_MUST_MATCH_CONTEXT"):
        backtest().run((bar(first),), lambda _context: signal, no_risk, OrderSide.BUY)


def test_backtest_fill_event_preserves_submission_audit_lifecycle():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = Signal(
        signal_id=UUID("22222222-2222-2222-2222-222222222222"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="0" * 64,
    )
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    calls = 0

    def strategy(_context):
        nonlocal calls
        calls += 1
        return signal if calls == 1 else None

    def risk(_signal):
        return assessment

    result = backtest().run((bar(first), bar(first + timedelta(minutes=1))), strategy, risk, OrderSide.BUY)

    assert calls == 2
    assert len(result.events) == 1
    assert tuple(event.event_type for event in result.events[0].result.audit_events) == (
        AuditEventType.SIGNAL_ACCEPTED,
        AuditEventType.RISK_APPROVED,
        AuditEventType.ORDER_CREATED,
        AuditEventType.FILL_CREATED,
        AuditEventType.PORTFOLIO_UPDATED,
    )
    assert result.events[0].result.fill is not None
    assert result.events[0].result.fill.fill_time == first + timedelta(minutes=1)


def test_backtest_rejects_duplicate_signal_id_before_second_submission():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = Signal(
        signal_id=UUID("33333333-3333-3333-3333-333333333333"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="1" * 64,
    )
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    def strategy(_context):
        return signal

    with pytest.raises(ValueError, match="DUPLICATE_SIGNAL_ID"):
        backtest().run((bar(first), bar(first + timedelta(minutes=1))), strategy, lambda _signal: assessment, OrderSide.BUY)


def test_backtest_delays_fill_until_quote_availability():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    available = second + timedelta(minutes=5)
    delayed = MarketBar(
        instrument_id=INSTRUMENT,
        event_time=second,
        available_time=available,
        ingestion_time=available,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )
    signal = Signal(
        signal_id=UUID("55555555-5555-5555-5555-555555555555"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="3" * 64,
    )
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    result = backtest().run(
        (bar(first), delayed),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert len(result.events) == 1
    assert result.events[0].event_time == available
    assert result.events[0].result.fill is not None
    assert result.events[0].result.fill.fill_time == available
    assert result.unfilled_order_ids == ()


def test_backtest_context_does_not_advance_past_current_event_for_delayed_data():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    delayed = MarketBar(
        instrument_id=INSTRUMENT,
        event_time=second,
        available_time=second + timedelta(minutes=5),
        ingestion_time=second + timedelta(minutes=5),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )
    observed = []

    def strategy(context):
        observed.append((context.as_of, tuple(b.event_time for b in context.bars)))
        return None

    backtest().run((bar(first), delayed), strategy, no_risk, OrderSide.BUY)

    assert observed == [
        (first, (first,)),
        (second, (first,)),
    ]


def test_backtest_valuation_adopts_delayed_mark_when_it_becomes_available():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    third = second + timedelta(minutes=1)
    fourth = third + timedelta(minutes=1)
    other_instrument = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    delayed = MarketBar(
        instrument_id=INSTRUMENT,
        event_time=third,
        available_time=fourth,
        ingestion_time=fourth,
        open=Decimal("130"),
        high=Decimal("131"),
        low=Decimal("129"),
        close=Decimal("130"),
        volume=Decimal("1000"),
    )
    later_other = MarketBar(
        instrument_id=other_instrument,
        event_time=fourth,
        available_time=fourth,
        ingestion_time=fourth,
        open=Decimal("50"),
        high=Decimal("51"),
        low=Decimal("49"),
        close=Decimal("50"),
        volume=Decimal("1000"),
    )
    signal = Signal(
        signal_id=UUID("66666666-6666-6666-6666-666666666666"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="4" * 64,
    )
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    result = backtest().run(
        (bar(first), bar(second), delayed, later_other),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert result.valuations[2].market_value == Decimal("100")
    assert result.valuations[3].market_value == Decimal("130")


def test_backtest_executes_delayed_quote_at_availability_without_later_market_event():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    available = second + timedelta(minutes=5)
    delayed = MarketBar(
        instrument_id=INSTRUMENT,
        event_time=second,
        available_time=available,
        ingestion_time=available,
        open=Decimal("120"),
        high=Decimal("121"),
        low=Decimal("119"),
        close=Decimal("120"),
        volume=Decimal("1000"),
    )
    signal = Signal(
        signal_id=UUID("77777777-7777-7777-7777-777777777777"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=first,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="5" * 64,
    )
    assessment = RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("1"),
    )

    result = backtest().run(
        (bar(first), delayed),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert len(result.events) == 1
    assert result.events[0].event_time == available
    assert result.events[0].result.fill is not None
    assert result.events[0].result.fill.fill_time == available
    assert result.unfilled_order_ids == ()
