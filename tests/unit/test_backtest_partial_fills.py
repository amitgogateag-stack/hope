from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INSTRUMENT = "11111111-1111-1111-1111-111111111111"
OTHER_INSTRUMENT = "33333333-3333-3333-3333-333333333333"


def bar(
    at: datetime,
    close: str = "100",
    instrument_id: str = INSTRUMENT,
) -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        instrument_id=instrument_id,
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
        signal_id=UUID("22222222-2222-2222-2222-222222222222"),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="test",
        decision_time=at,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="7" * 64,
    )


def make_assessment(signal: Signal) -> RiskAssessment:
    return RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal("10"),
    )


def test_backtest_accumulates_deterministic_partial_fills_until_order_is_complete():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = make_signal(first)
    assessment = make_assessment(signal)
    result = DeterministicBacktest(
        Decimal("10000"), CostModel(version="test"), max_fill_quantity=Decimal("4")
    ).run(
        (
            bar(first),
            bar(first + timedelta(minutes=1), "101"),
            bar(first + timedelta(minutes=2), "102"),
            bar(first + timedelta(minutes=3), "103"),
        ),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert len(result.events) == 3
    assert [event.result.fill.quantity for event in result.events] == [
        Decimal("4"), Decimal("4"), Decimal("2")
    ]
    assert len({event.result.fill.fill_id for event in result.events}) == 3
    assert result.final_state.positions[UUID(INSTRUMENT)].quantity == Decimal("10")
    assert result.unfilled_order_ids == ()


def test_backtest_does_not_reuse_the_same_quote_for_a_later_partial_fill():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    quote_time = first + timedelta(minutes=1)
    later_clock_time = first + timedelta(minutes=2)
    signal = make_signal(first)
    assessment = make_assessment(signal)
    result = DeterministicBacktest(
        Decimal("10000"), CostModel(version="test"), max_fill_quantity=Decimal("4")
    ).run(
        (
            bar(first),
            bar(quote_time, "101"),
            bar(later_clock_time, "201", OTHER_INSTRUMENT),
        ),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert len(result.events) == 1
    assert result.events[0].bar.event_time == quote_time
    assert result.final_state.positions[UUID(INSTRUMENT)].quantity == Decimal("4")
    assert result.unfilled_order_ids == (result.events[0].result.order_id,)


def test_backtest_preserves_partially_filled_order_when_series_ends():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    signal = make_signal(first)
    assessment = make_assessment(signal)
    result = DeterministicBacktest(
        Decimal("10000"), CostModel(version="test"), max_fill_quantity=Decimal("4")
    ).run(
        (bar(first), bar(first + timedelta(minutes=1), "101")),
        lambda context: signal if context.as_of == first else None,
        lambda _signal: assessment,
        OrderSide.BUY,
    )

    assert len(result.events) == 1
    assert result.events[0].result.fill.quantity == Decimal("4")
    assert result.final_state.positions[UUID(INSTRUMENT)].quantity == Decimal("4")
    assert result.unfilled_order_ids == (
        result.events[0].result.order_id,
    )


def test_backtest_rejects_non_positive_partial_fill_limit():
    with pytest.raises(ValueError, match="MAX_FILL_QUANTITY_MUST_BE_POSITIVE"):
        DeterministicBacktest(
            Decimal("10000"), CostModel(version="test"), max_fill_quantity=Decimal("0")
        )
