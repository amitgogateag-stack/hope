from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.replay import ExecutionReplayError, replay_order
from hope.domain.execution.timeline import ExecutionTimeline


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def make_order(quantity="10"):
    return Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=uuid4(), side=OrderSide.BUY,
                 quantity=Decimal(quantity), environment=Environment.PAPER)


def make_fill(order, qty, minute=0, fill_id=None):
    fill_time = BASE_TIME + timedelta(minutes=minute)
    quote = ExecutionQuote(order.instrument_id, fill_time, Decimal("100"), Decimal("101"))
    timeline = ExecutionTimeline.from_decision(fill_time, latency=timedelta(0))
    return simulate_market_fill(order, quote, fill_id or uuid4(), CostModel("test"), Decimal(qty), timeline=timeline)


def test_replay_reconciles_multi_fill_order():
    order = make_order("10")
    result = replay_order(order, [make_fill(order, "3"), make_fill(order, "7", 1)], initial_cash=Decimal("10000"))
    assert result.fills_applied == 2
    assert result.filled_quantity == Decimal("10")
    assert result.state.lifecycle.status == "FILLED"
    assert result.state.portfolio.positions[order.instrument_id].quantity == Decimal("10")


def test_replay_rejects_duplicate_fill_event_before_state_mutation():
    order = make_order("10")
    fill = make_fill(order, "5")
    with pytest.raises(ExecutionReplayError, match="DUPLICATE_FILL_EVENT"):
        replay_order(order, [fill, fill], initial_cash=Decimal("10000"))


def test_replay_rejects_cross_order_fill():
    order = make_order("10")
    other = make_order("10")
    fill = make_fill(other, "5")
    with pytest.raises(ExecutionReplayError, match="FILL_ORDER_MISMATCH"):
        replay_order(order, [fill], initial_cash=Decimal("10000"))


def test_replay_rejects_excess_quantity():
    order = make_order("10")
    with pytest.raises(ExecutionReplayError, match="FILL_EXCEEDS_REMAINING_ORDER_QUANTITY"):
        replay_order(order, [make_fill(order, "6"), make_fill(order, "5", 1)], initial_cash=Decimal("10000"))
