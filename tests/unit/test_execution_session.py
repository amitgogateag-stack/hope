from dataclasses import replace
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


@pytest.mark.parametrize(
    ("fill_time", "error_code"),
    (
        (None, "FILL_TIME_REQUIRED"),
        (datetime(2026, 1, 1, 14, 0), "FILL_TIME_MUST_BE_TIMEZONE_AWARE"),
    ),
)
def test_fill_requires_timezone_aware_time_before_state_mutation(fill_time, error_code):
    order = make_order("10")
    session = ExecutionSession(OrderLifecycle(order), PortfolioLedger(Decimal("10000")))
    malformed = replace(make_fill(order, "1"), fill_time=fill_time)
    before = session.state

    with pytest.raises(ExecutionSessionError, match=error_code):
        session.apply_fill(malformed)

    assert session.state == before


@pytest.mark.parametrize(
    ("field_name", "error_code"),
    (
        ("quantity", "FILL_QUANTITY_MUST_BE_FINITE"),
        ("price", "FILL_PRICE_MUST_BE_FINITE"),
        ("commission", "FILL_COMMISSION_MUST_BE_FINITE"),
        ("slippage", "FILL_SLIPPAGE_MUST_BE_FINITE"),
    ),
)
def test_fill_requires_finite_numeric_fields_before_state_mutation(field_name, error_code):
    order = make_order("10")
    session = ExecutionSession(OrderLifecycle(order), PortfolioLedger(Decimal("10000")))
    malformed = replace(make_fill(order, "1"), **{field_name: Decimal("NaN")})
    before = session.state

    with pytest.raises(ExecutionSessionError, match=error_code):
        session.apply_fill(malformed)

    assert session.state == before


def test_fill_requires_cost_model_version_before_state_mutation():
    order = make_order("10")
    session = ExecutionSession(OrderLifecycle(order), PortfolioLedger(Decimal("10000")))
    malformed = replace(make_fill(order, "1"), cost_model_version="   ")
    before = session.state

    with pytest.raises(ExecutionSessionError, match="FILL_COST_MODEL_VERSION_REQUIRED"):
        session.apply_fill(malformed)

    assert session.state == before


def test_partial_fill_can_be_cancelled_without_portfolio_mutation_and_blocks_later_fill():
    order = make_order("10")
    session = ExecutionSession(OrderLifecycle(order), PortfolioLedger(Decimal("10000")))
    partially_filled = session.apply_fill(make_fill(order, "4"))
    portfolio_before_cancel = partially_filled.portfolio
    fill_ids_before_cancel = partially_filled.fill_ids

    cancelled = session.cancel()

    assert cancelled.lifecycle.status == "CANCELLED"
    assert cancelled.lifecycle.filled_quantity == Decimal("4")
    assert cancelled.lifecycle.remaining_quantity == Decimal("6")
    assert cancelled.portfolio == portfolio_before_cancel
    assert cancelled.fill_ids == fill_ids_before_cancel

    with pytest.raises(ExecutionSessionError, match="TERMINAL_ORDER_CANNOT_FILL"):
        session.apply_fill(make_fill(order, "1", 1))

    assert session.state == cancelled
