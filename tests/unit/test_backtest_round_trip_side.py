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


def make_bar(at: datetime, close: str) -> MarketBar:
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


def make_signal(signal_id: str, at: datetime, signal_type: SignalType) -> Signal:
    return Signal(
        signal_id=UUID(signal_id),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="round-trip-test",
        decision_time=at,
        signal_type=signal_type,
        conviction=Decimal("0.5"),
        inputs_hash=("1" if signal_type is SignalType.ENTRY else "2") * 64,
    )


@pytest.mark.parametrize(
    ("entry_side", "prices", "expected_fill_sides"),
    (
        (OrderSide.BUY, ("100", "110", "120"), (OrderSide.BUY, OrderSide.SELL)),
        (OrderSide.SELL, ("120", "110", "100"), (OrderSide.SELL, OrderSide.BUY)),
    ),
)
def test_backtest_exit_uses_opposite_side_and_closes_round_trip(
    entry_side: OrderSide,
    prices: tuple[str, str, str],
    expected_fill_sides: tuple[OrderSide, OrderSide],
):
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    third = second + timedelta(minutes=1)
    entry = make_signal("11111111-2222-3333-4444-555555555555", first, SignalType.ENTRY)
    exit_signal = make_signal("66666666-7777-8888-9999-aaaaaaaaaaaa", second, SignalType.EXIT)

    def strategy(context):
        if context.as_of == first:
            return entry
        if context.as_of == second:
            return exit_signal
        return None

    def risk(signal):
        return RiskAssessment(
            signal_id=signal.signal_id,
            decision=RiskDecision.APPROVE,
            reason_code="TEST_APPROVED",
            approved_quantity=Decimal("1"),
        )

    result = DeterministicBacktest(Decimal("10000"), CostModel(version="test")).run(
        (make_bar(first, prices[0]), make_bar(second, prices[1]), make_bar(third, prices[2])),
        strategy,
        risk,
        entry_side,
    )

    assert len(result.decisions) == 2
    assert tuple(decision.result.intent.side for decision in result.decisions) == expected_fill_sides
    assert len(result.events) == 2
    assert tuple(event.result.fill.side for event in result.events) == expected_fill_sides

    position = result.final_state.positions[UUID(INSTRUMENT)]
    assert position.quantity == Decimal("0")
    assert position.realized_pnl == Decimal("10")
    assert result.final_state.cash == Decimal("10010")
