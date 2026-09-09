from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.replay import replay_order
from hope.domain.execution.session import ExecutionSessionError
from hope.domain.execution.timeline import ExecutionTimeline


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def make_fill(order, quantity, minute):
    fill_time = BASE_TIME + timedelta(minutes=minute)
    quote = ExecutionQuote(order.instrument_id, fill_time, Decimal("100"), Decimal("101"))
    timeline = ExecutionTimeline.from_decision(fill_time, latency=timedelta(0))
    return simulate_market_fill(order, quote, uuid4(), CostModel("restart-test"), Decimal(quantity), timeline=timeline)


def make_order():
    return Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=uuid4(), side=OrderSide.BUY,
                 quantity=Decimal("10"), environment=Environment.PAPER)


def test_replayed_partial_order_returns_session_that_can_resume_after_restart():
    order = make_order()
    first_fill = make_fill(order, "4", 0)
    restored = replay_order(order, [first_fill], initial_cash=Decimal("10000"))

    assert restored.session is not None
    assert restored.session.state == restored.state
    assert restored.session.state.lifecycle.status == "PARTIALLY_FILLED"
    assert restored.session.state.lifecycle.remaining_quantity == Decimal("6")
    assert restored.session.state.last_fill_time == first_fill.fill_time

    resumed = restored.session.apply_fill(make_fill(order, "6", 1))
    assert resumed.lifecycle.status == "FILLED"
    assert resumed.lifecycle.remaining_quantity == Decimal("0")
    assert resumed.portfolio.positions[order.instrument_id].quantity == Decimal("10")
    assert first_fill.fill_id in resumed.fill_ids
    assert resumed.last_fill_time == BASE_TIME + timedelta(minutes=1)


def test_replayed_session_rejects_fill_older_than_last_replayed_fill():
    order = make_order()
    first_fill = make_fill(order, "4", 2)
    restored = replay_order(order, [first_fill], initial_cash=Decimal("10000"))
    before = restored.session.state

    with pytest.raises(ExecutionSessionError, match="FILL_EVENTS_OUT_OF_TIME_ORDER"):
        restored.session.apply_fill(make_fill(order, "6", 1))

    assert restored.session.state == before
