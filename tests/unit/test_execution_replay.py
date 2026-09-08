from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.models import ExecutionCancellation
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


def make_cancellation(order, qty, minute=2):
    return ExecutionCancellation(
        order_id=order.order_id,
        signal_id=order.signal_id,
        instrument_id=order.instrument_id,
        environment=order.environment,
        reason_code="REPLAY_CANCELLED",
        cancellation_time=BASE_TIME + timedelta(minutes=minute),
        cancelled_quantity=Decimal(qty),
    )


def test_replay_reconciles_multi_fill_order():
    order = make_order("10")
    result = replay_order(order, [make_fill(order, "3"), make_fill(order, "7", 1)], initial_cash=Decimal("10000"))
    assert result.fills_applied == 2
    assert result.filled_quantity == Decimal("10")
    assert result.cancellation_applied is False
    assert result.state.lifecycle.status == "FILLED"
    assert result.state.portfolio.positions[order.instrument_id].quantity == Decimal("10")


def test_replay_reconstructs_partial_fill_then_cancellation():
    order = make_order("10")
    result = replay_order(
        order,
        [make_fill(order, "4")],
        cancellation=make_cancellation(order, "6"),
        initial_cash=Decimal("10000"),
    )

    assert result.fills_applied == 1
    assert result.filled_quantity == Decimal("4")
    assert result.cancellation_applied is True
    assert result.state.lifecycle.status == "CANCELLED"
    assert result.state.lifecycle.remaining_quantity == Decimal("6")
    assert result.state.portfolio.positions[order.instrument_id].quantity == Decimal("4")


def test_replay_reconstructs_unfilled_cancellation_with_order_time():
    order = make_order("10")
    result = replay_order(
        order,
        [],
        cancellation=make_cancellation(order, "10", minute=2),
        order_time=BASE_TIME + timedelta(minutes=1),
        initial_cash=Decimal("10000"),
    )

    assert result.fills_applied == 0
    assert result.filled_quantity == Decimal("0")
    assert result.cancellation_applied is True
    assert result.state.lifecycle.status == "CANCELLED"
    assert result.state.lifecycle.remaining_quantity == Decimal("10")
    assert result.state.portfolio.cash == Decimal("10000")


def test_replay_requires_order_time_for_unfilled_cancellation():
    order = make_order("10")
    with pytest.raises(ExecutionReplayError, match="CANCELLATION_ORDER_TIME_REQUIRED"):
        replay_order(
            order,
            [],
            cancellation=make_cancellation(order, "10"),
            initial_cash=Decimal("10000"),
        )


def test_replay_rejects_unfilled_cancellation_before_order_time():
    order = make_order("10")
    with pytest.raises(ExecutionReplayError, match="CANCELLATION_PRECEDES_ORDER_TIME"):
        replay_order(
            order,
            [],
            cancellation=make_cancellation(order, "10", minute=1),
            order_time=BASE_TIME + timedelta(minutes=2),
            initial_cash=Decimal("10000"),
        )


def test_replay_rejects_cancellation_quantity_that_does_not_match_remaining_order():
    order = make_order("10")
    with pytest.raises(ExecutionReplayError, match="CANCELLATION_REMAINING_QUANTITY_MISMATCH"):
        replay_order(
            order,
            [make_fill(order, "4")],
            cancellation=make_cancellation(order, "5"),
            initial_cash=Decimal("10000"),
        )


def test_replay_rejects_cancellation_before_replayed_fill():
    order = make_order("10")
    with pytest.raises(ExecutionReplayError, match="CANCELLATION_PRECEDES_REPLAYED_FILL"):
        replay_order(
            order,
            [make_fill(order, "4", minute=2)],
            cancellation=make_cancellation(order, "6", minute=1),
            initial_cash=Decimal("10000"),
        )


def test_replay_rejects_fill_events_out_of_time_order():
    order = make_order("10")
    later = make_fill(order, "4", minute=2)
    earlier = make_fill(order, "6", minute=1)

    with pytest.raises(ExecutionReplayError, match="FILL_EVENTS_OUT_OF_TIME_ORDER"):
        replay_order(order, [later, earlier], initial_cash=Decimal("10000"))


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
