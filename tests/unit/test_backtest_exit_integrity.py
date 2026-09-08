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


def make_signal(signal_id: str, at: datetime, signal_type: SignalType) -> Signal:
    return Signal(
        signal_id=UUID(signal_id),
        instrument_id=UUID(INSTRUMENT),
        strategy_version="exit-integrity-test",
        decision_time=at,
        signal_type=signal_type,
        conviction=Decimal("0.5"),
        inputs_hash=("3" if signal_type is SignalType.ENTRY else "4") * 64,
    )


def approve(signal: Signal, quantity: str = "1") -> RiskAssessment:
    return RiskAssessment(
        signal_id=signal.signal_id,
        decision=RiskDecision.APPROVE,
        reason_code="TEST_APPROVED",
        approved_quantity=Decimal(quantity),
    )


def test_backtest_exit_requires_an_open_position():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    exit_signal = make_signal(
        "11111111-2222-3333-4444-555555555555",
        first,
        SignalType.EXIT,
    )

    with pytest.raises(ValueError, match="EXIT_REQUIRES_OPEN_POSITION"):
        DeterministicBacktest(Decimal("10000"), CostModel(version="test")).run(
            (make_bar(first),),
            lambda context: exit_signal if context.as_of == first else None,
            lambda signal: approve(signal),
            OrderSide.BUY,
        )


def test_backtest_exit_quantity_cannot_exceed_open_position():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    entry = make_signal(
        "11111111-2222-3333-4444-555555555555",
        first,
        SignalType.ENTRY,
    )
    exit_signal = make_signal(
        "66666666-7777-8888-9999-aaaaaaaaaaaa",
        second,
        SignalType.EXIT,
    )

    def strategy(context):
        if context.as_of == first:
            return entry
        if context.as_of == second:
            return exit_signal
        return None

    def risk(signal):
        return approve(signal, "2" if signal.signal_type is SignalType.EXIT else "1")

    with pytest.raises(ValueError, match="EXIT_QUANTITY_EXCEEDS_POSITION"):
        DeterministicBacktest(Decimal("10000"), CostModel(version="test")).run(
            (make_bar(first, "100"), make_bar(second, "101")),
            strategy,
            risk,
            OrderSide.BUY,
        )


def test_concurrent_exit_orders_are_rechecked_at_fill_time():
    first = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    second = first + timedelta(minutes=1)
    third = second + timedelta(minutes=1)
    entry = make_signal(
        "11111111-2222-3333-4444-555555555555",
        first,
        SignalType.ENTRY,
    )
    exit_a = make_signal(
        "66666666-7777-8888-9999-aaaaaaaaaaaa",
        second,
        SignalType.EXIT,
    )
    exit_b = make_signal(
        "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        second,
        SignalType.EXIT,
    )

    def strategy(context):
        if context.as_of == first:
            return entry
        if context.as_of == second:
            return (exit_a, exit_b)
        return None

    with pytest.raises(ValueError, match="EXIT_REQUIRES_OPEN_POSITION"):
        DeterministicBacktest(Decimal("10000"), CostModel(version="test")).run(
            (make_bar(first, "100"), make_bar(second, "101"), make_bar(third, "102")),
            strategy,
            lambda signal: approve(signal),
            OrderSide.BUY,
        )
