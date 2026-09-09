from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.replay import ExecutionReplayError, replay_order
from hope.domain.execution.session import ExecutionSessionError
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioState, PositionState


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


def test_replay_restores_preorder_portfolio_before_resuming_execution():
    order = make_order()
    baseline = PortfolioState(
        cash=Decimal("5000"),
        positions={
            order.instrument_id: PositionState(
                instrument_id=order.instrument_id,
                quantity=Decimal("3"),
                average_price=Decimal("90"),
                realized_pnl=Decimal("12"),
                total_commission=Decimal("1"),
            )
        },
    )
    first_fill = make_fill(order, "4", 0)

    restored = replay_order(order, [first_fill], initial_portfolio=baseline)

    assert restored.session is not None
    assert restored.state.portfolio.positions[order.instrument_id].quantity == Decimal("7")
    assert baseline.positions[order.instrument_id].quantity == Decimal("3")

    resumed = restored.session.apply_fill(make_fill(order, "6", 1))
    assert resumed.lifecycle.status == "FILLED"
    assert resumed.portfolio.positions[order.instrument_id].quantity == Decimal("13")


def test_replay_rejects_ambiguous_cash_and_portfolio_baselines():
    order = make_order()
    baseline = PortfolioState(cash=Decimal("5000"), positions={})

    with pytest.raises(ExecutionReplayError, match="INITIAL_CASH_AND_PORTFOLIO_MUTUALLY_EXCLUSIVE"):
        replay_order(order, [], order_time=BASE_TIME, initial_cash=Decimal("10000"), initial_portfolio=baseline)


def test_replayed_session_rejects_fill_older_than_last_replayed_fill():
    order = make_order()
    first_fill = make_fill(order, "4", 2)
    restored = replay_order(order, [first_fill], initial_cash=Decimal("10000"))
    before = restored.session.state

    with pytest.raises(ExecutionSessionError, match="FILL_EVENTS_OUT_OF_TIME_ORDER"):
        restored.session.apply_fill(make_fill(order, "6", 1))

    assert restored.session.state == before


def test_replayed_open_session_preserves_order_time_for_future_fills():
    order = make_order()
    order_time = BASE_TIME
    restored = replay_order(order, [], order_time=order_time, initial_cash=Decimal("10000"))
    before = restored.session.state

    assert before.order_time == order_time
    with pytest.raises(ExecutionSessionError, match="FILL_PRECEDES_ORDER_TIME"):
        restored.session.apply_fill(make_fill(order, "1", -1))

    assert restored.session.state == before


def test_zero_fill_open_replay_without_order_time_is_snapshot_only():
    order = make_order()
    restored = replay_order(order, [], initial_cash=Decimal("10000"))

    assert restored.state.lifecycle.status == "OPEN"
    assert restored.state.lifecycle.filled_quantity == Decimal("0")
    assert restored.state.order_time is None
    assert restored.state.last_fill_time is None
    assert restored.session is None


@pytest.mark.parametrize(
    ("fill_time", "error_code"),
    (
        (None, "FILL_TIME_REQUIRED"),
        (datetime(2026, 1, 1, 14, 0), "FILL_TIME_MUST_BE_TIMEZONE_AWARE"),
    ),
)
def test_replay_rejects_fill_without_usable_event_time(fill_time, error_code):
    order = make_order()
    malformed = replace(make_fill(order, "4", 0), fill_time=fill_time)

    with pytest.raises(ExecutionReplayError, match=error_code):
        replay_order(order, [malformed], initial_cash=Decimal("10000"))


@pytest.mark.parametrize(
    ("field_name", "error_code"),
    (
        ("quantity", "FILL_QUANTITY_MUST_BE_FINITE"),
        ("price", "FILL_PRICE_MUST_BE_FINITE"),
        ("commission", "FILL_COMMISSION_MUST_BE_FINITE"),
        ("slippage", "FILL_SLIPPAGE_MUST_BE_FINITE"),
    ),
)
def test_replay_rejects_non_finite_fill_numeric_fields(field_name, error_code):
    order = make_order()
    malformed = replace(make_fill(order, "4", 0), **{field_name: Decimal("NaN")})

    with pytest.raises(ExecutionReplayError, match=error_code):
        replay_order(order, [malformed], initial_cash=Decimal("10000"))
