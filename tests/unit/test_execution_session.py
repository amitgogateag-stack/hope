from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.lifecycle import OrderLifecycle
from hope.domain.execution.session import ExecutionSession, ExecutionSessionError
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def make_order(quantity="10"):
    return Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=uuid4(), side=OrderSide.BUY,
                 quantity=Decimal(quantity), environment=Environment.PAPER)


def make_fill(order, qty, minute=0, fill_id=None):
    fill_time = BASE_TIME + timedelta(minutes=minute)
    quote = ExecutionQuote(order.instrument_id, fill_time, Decimal("100"), Decimal("101"))
    timeline = ExecutionTimeline.from_decision(fill_time, latency=timedelta(0))
    return simulate_market_fill(order, quote, fill_id or uuid4(), CostModel("test"), Decimal(qty), timeline=timeline)


def test_multi_fill_session_reconciles_to_order_quantity():
    order = make_order("10")
    session = ExecutionSession(OrderLifecycle(order), PortfolioLedger(Decimal("10000")))
    state = session.apply_fill(make_fill(order, "3"))
    assert state.lifecycle.remaining_quantity == Decimal("7")
    state = session.apply_fill(make_fill(order, "7", 1))
    assert state.lifecycle.status == "FILLED"
    assert state.portfolio.positions[order.instrument_id].quantity == Decimal("10")


def test_excess_fill_does_not_mutate_session():
    order = make_order("10")
    session = ExecutionSession(OrderLifecycle(order), PortfolioLedger(Decimal("10000")))
    session.apply_fill(make_fill(order, "6"))
    before = session.state
    with pytest.raises(ExecutionSessionError, match="FILL_EXCEEDS_REMAINING_ORDER_QUANTITY"):
        session.apply_fill(make_fill(order, "5", 1))
    after = session.state
    assert after.lifecycle == before.lifecycle
    assert after.portfolio == before.portfolio
    assert after.fill_ids == before.fill_ids


def test_duplicate_fill_does_not_mutate_session():
    order = make_order("10")
    session = ExecutionSession(OrderLifecycle(order), PortfolioLedger(Decimal("10000")))
    fill = make_fill(order, "5")
    session.apply_fill(fill)
    before = session.state
    with pytest.raises(ExecutionSessionError, match="DUPLICATE_FILL"):
        session.apply_fill(fill)
    after = session.state
    assert after == before
