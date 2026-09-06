from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Fill, Order, OrderSide, simulate_market_fill
from hope.domain.execution.lifecycle import OrderLifecycle, OrderLifecycleError
from hope.domain.execution.timeline import ExecutionTimeline


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def make_order():
    return Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=uuid4(), side=OrderSide.BUY, quantity=Decimal("10"), environment=Environment.PAPER)


def make_fill(order, qty, minute=0):
    fill_time = BASE_TIME + timedelta(minutes=minute)
    quote = ExecutionQuote(order.instrument_id, fill_time, Decimal("99"), Decimal("100"))
    timeline = ExecutionTimeline.from_decision(fill_time, latency=timedelta(0))
    simulated = simulate_market_fill(order, quote, uuid4(), CostModel("test"), timeline=timeline)
    return Fill(simulated.fill_id, simulated.order_id, simulated.signal_id, simulated.instrument_id,
                simulated.side, Decimal(qty), simulated.price, simulated.commission, simulated.slippage,
                simulated.cost_model_version, simulated.fill_time)


def test_partial_then_full_fill_tracks_remaining_quantity():
    order = make_order()
    state = OrderLifecycle(order)
    state = state.apply_fill(make_fill(order, Decimal("4")))
    assert state.status == "PARTIALLY_FILLED"
    assert state.remaining_quantity == Decimal("6")
    state = state.apply_fill(make_fill(order, Decimal("6"), 1))
    assert state.status == "FILLED"
    assert state.remaining_quantity == Decimal("0")


def test_fill_cannot_exceed_remaining_quantity():
    order = make_order()
    with pytest.raises(OrderLifecycleError, match="FILL_EXCEEDS_REMAINING_ORDER_QUANTITY"):
        OrderLifecycle(order).apply_fill(make_fill(order, Decimal("11")))


def test_cancelled_order_cannot_fill():
    order = make_order()
    state = OrderLifecycle(order).cancel()
    with pytest.raises(OrderLifecycleError, match="TERMINAL_ORDER_CANNOT_FILL"):
        state.apply_fill(make_fill(order, Decimal("1")))


def test_reject_only_before_any_fill():
    order = make_order()
    state = OrderLifecycle(order).reject()
    assert state.status == "REJECTED"
    with pytest.raises(OrderLifecycleError, match="TERMINAL_ORDER_CANNOT_FILL"):
        state.apply_fill(make_fill(order, Decimal("1")))


def test_duplicate_fill_is_rejected_by_lifecycle():
    order = make_order()
    quote = ExecutionQuote(order.instrument_id, BASE_TIME, Decimal("100"), Decimal("100"))
    timeline = ExecutionTimeline.from_decision(BASE_TIME, latency=timedelta(0))
    fill = simulate_market_fill(order, quote, uuid4(), CostModel("test"), timeline=timeline)
    lifecycle = OrderLifecycle(order).apply_fill(fill)
    with pytest.raises(OrderLifecycleError, match="DUPLICATE_FILL"):
        lifecycle.apply_fill(fill)


def test_fill_with_different_signal_is_rejected():
    order = make_order()
    quote = ExecutionQuote(order.instrument_id, BASE_TIME, Decimal("99"), Decimal("100"))
    timeline = ExecutionTimeline.from_decision(BASE_TIME, latency=timedelta(0))
    simulated = simulate_market_fill(order, quote, uuid4(), CostModel("test"), timeline=timeline)
    mismatched = Fill(simulated.fill_id, simulated.order_id, uuid4(), simulated.instrument_id,
                      simulated.side, simulated.quantity, simulated.price, simulated.commission,
                      simulated.slippage, simulated.cost_model_version, simulated.fill_time)
    with pytest.raises(OrderLifecycleError, match="FILL_SIGNAL_MISMATCH"):
        OrderLifecycle(order).apply_fill(mismatched)


def test_fill_with_different_instrument_is_rejected():
    order = make_order()
    quote = ExecutionQuote(order.instrument_id, BASE_TIME, Decimal("99"), Decimal("100"))
    timeline = ExecutionTimeline.from_decision(BASE_TIME, latency=timedelta(0))
    simulated = simulate_market_fill(order, quote, uuid4(), CostModel("test"), timeline=timeline)
    mismatched = Fill(simulated.fill_id, simulated.order_id, simulated.signal_id, uuid4(),
                      simulated.side, simulated.quantity, simulated.price, simulated.commission,
                      simulated.slippage, simulated.cost_model_version, simulated.fill_time)
    with pytest.raises(OrderLifecycleError, match="FILL_INSTRUMENT_MISMATCH"):
        OrderLifecycle(order).apply_fill(mismatched)
