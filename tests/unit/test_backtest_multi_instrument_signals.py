from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from hope.application.backtests.engine import DeterministicBacktest
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


UTC = timezone.utc
INSTRUMENT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
INSTRUMENT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def make_bar(instrument_id: str, at: datetime, close: str) -> MarketBar:
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


def make_signal(signal_id: str, instrument_id: str, at: datetime) -> Signal:
    return Signal(
        signal_id=UUID(signal_id),
        instrument_id=UUID(instrument_id),
        strategy_version="multi-instrument-test",
        decision_time=at,
        signal_type=SignalType.ENTRY,
        conviction=Decimal("0.5"),
        inputs_hash="a" * 64,
    )


def test_backtest_accepts_multi_instrument_signal_batch_independent_of_bar_order():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    signal_a = make_signal(
        "11111111-1111-1111-1111-111111111111",
        INSTRUMENT_A,
        first,
    )
    signal_b = make_signal(
        "22222222-2222-2222-2222-222222222222",
        INSTRUMENT_B,
        first,
    )
    bars = (
        make_bar(INSTRUMENT_B, first, "200"),
        make_bar(INSTRUMENT_A, first, "100"),
        make_bar(INSTRUMENT_B, second, "201"),
        make_bar(INSTRUMENT_A, second, "101"),
    )

    def strategy(context):
        if context.as_of == first:
            return (signal_b, signal_a)
        return None

    def risk(signal: Signal) -> RiskAssessment:
        return RiskAssessment(
            signal_id=signal.signal_id,
            decision=RiskDecision.APPROVE,
            reason_code="TEST_APPROVED",
            approved_quantity=Decimal("1"),
        )

    result = DeterministicBacktest(
        Decimal("10000"),
        CostModel(version="test"),
    ).run(bars, strategy, risk, OrderSide.BUY)

    assert len(result.events) == 2
    assert result.unfilled_order_ids == ()
    assert tuple(event.event_time for event in result.events) == (second, second)
    assert {event.result.fill.instrument_id for event in result.events} == {
        UUID(INSTRUMENT_A),
        UUID(INSTRUMENT_B),
    }
    assert result.final_state.positions[UUID(INSTRUMENT_A)].quantity == Decimal("1")
    assert result.final_state.positions[UUID(INSTRUMENT_B)].quantity == Decimal("1")
