from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.timeline import ExecutionTimeline


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def make_order():
    return Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=uuid4(), side=OrderSide.BUY,
                 quantity=Decimal("10"), environment=Environment.PAPER)


def make_timeline(quote_time: datetime) -> ExecutionTimeline:
    return ExecutionTimeline.from_decision(
        quote_time - timedelta(minutes=1), latency=timedelta(0)
    )


def test_buy_fill_uses_ask_and_adds_slippage_and_commission():
    instrument = uuid4()
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=instrument, side=OrderSide.BUY,
                  quantity=Decimal("10"), environment=Environment.PAPER)
    quote = ExecutionQuote(instrument, quote_time, Decimal("99"), Decimal("100"))
    model = CostModel("cost-v1", Decimal("0.001"), Decimal("10"))

    fill = simulate_market_fill(order, quote, uuid4(), model, timeline=make_timeline(quote_time))

    assert fill.price == Decimal("100.100")
    assert fill.commission == Decimal("1.001000")
    assert fill.slippage == Decimal("1.00")
    assert fill.cost_model_version == "cost-v1"


def test_sell_fill_uses_bid_and_applies_adverse_slippage():
    instrument = uuid4()
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=instrument, side=OrderSide.SELL,
                  quantity=Decimal("5"), environment=Environment.BACKTEST)
    quote = ExecutionQuote(instrument, quote_time, Decimal("100"), Decimal("101"))
    fill = simulate_market_fill(
        order, quote, uuid4(), CostModel("v1", Decimal("0"), Decimal("25")),
        timeline=make_timeline(quote_time),
    )
    assert fill.price == Decimal("99.75")


def test_quote_instrument_mismatch_is_rejected():
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=uuid4(), side=OrderSide.BUY,
                  quantity=Decimal("1"), environment=Environment.PAPER)
    quote = ExecutionQuote(uuid4(), quote_time, Decimal("1"), Decimal("1.01"))
    with pytest.raises(ValueError, match="ORDER_QUOTE_INSTRUMENT_MISMATCH"):
        simulate_market_fill(order, quote, uuid4(), CostModel("v1"), timeline=make_timeline(quote_time))


def test_crossed_quote_is_rejected():
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="QUOTE_ASK_BELOW_BID"):
        ExecutionQuote(uuid4(), quote_time, Decimal("101"), Decimal("100"))


def test_simulator_supports_partial_fill_quantity():
    order = make_order()
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    quote = ExecutionQuote(order.instrument_id, quote_time, Decimal("99"), Decimal("100"))
    fill = simulate_market_fill(
        order, quote, uuid4(), CostModel("test"), quantity=Decimal("4"),
        timeline=make_timeline(quote_time),
    )
    assert fill.quantity == Decimal("4")


def test_simulator_rejects_fill_quantity_above_order():
    order = make_order()
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    quote = ExecutionQuote(order.instrument_id, quote_time, Decimal("99"), Decimal("100"))
    with pytest.raises(ValueError, match="FILL_QUANTITY_EXCEEDS_ORDER_QUANTITY"):
        simulate_market_fill(
            order, quote, uuid4(), CostModel("test"), quantity=Decimal("11"),
            timeline=make_timeline(quote_time),
        )


def test_quote_event_time_must_be_timezone_aware():
    with pytest.raises(ValueError, match="QUOTE_EVENT_TIME_MUST_BE_TIMEZONE_AWARE"):
        ExecutionQuote(uuid4(), datetime(2026, 1, 1, 14, 1), Decimal("1"), Decimal("1"))


def test_quote_available_time_must_be_timezone_aware():
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="QUOTE_AVAILABLE_TIME_MUST_BE_TIMEZONE_AWARE"):
        ExecutionQuote(
            uuid4(), quote_time, Decimal("1"), Decimal("1"),
            available_time=datetime(2026, 1, 1, 14, 2),
        )


def test_quote_available_time_cannot_precede_event_time():
    quote_time = datetime(2026, 1, 1, 14, 2, tzinfo=timezone.utc)
    available_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="QUOTE_AVAILABLE_TIME_PRECEDES_EVENT_TIME"):
        ExecutionQuote(
            uuid4(), quote_time, Decimal("1"), Decimal("1"),
            available_time=available_time,
        )


def test_fill_rejects_quote_before_timeline_eligibility():
    instrument = uuid4()
    decision_time = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=instrument, side=OrderSide.BUY,
                  quantity=Decimal("1"), environment=Environment.BACKTEST)
    quote = ExecutionQuote(instrument, quote_time, Decimal("99"), Decimal("100"))
    timeline = ExecutionTimeline.from_decision(decision_time, latency=timedelta(minutes=2))
    with pytest.raises(ValueError, match="QUOTE_PRECEDES_FILL_ELIGIBILITY"):
        simulate_market_fill(order, quote, uuid4(), CostModel("v1"), timeline=timeline)


def test_delayed_quote_is_valid_when_available_by_fill_time():
    instrument = uuid4()
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    available_time = datetime(2026, 1, 1, 14, 2, tzinfo=timezone.utc)
    fill_time = datetime(2026, 1, 1, 14, 3, tzinfo=timezone.utc)
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=instrument, side=OrderSide.BUY,
                  quantity=Decimal("1"), environment=Environment.BACKTEST)
    quote = ExecutionQuote(
        instrument, quote_time, Decimal("99"), Decimal("100"), available_time=available_time
    )
    timeline = ExecutionTimeline.from_decision(quote_time, latency=timedelta(0)).with_fill_time(fill_time)

    fill = simulate_market_fill(order, quote, uuid4(), CostModel("v1"), timeline=timeline)

    assert fill.fill_time == fill_time


def test_delayed_quote_is_rejected_before_availability():
    instrument = uuid4()
    quote_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    available_time = datetime(2026, 1, 1, 14, 2, tzinfo=timezone.utc)
    fill_time = datetime(2026, 1, 1, 14, 1, tzinfo=timezone.utc)
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=instrument, side=OrderSide.BUY,
                  quantity=Decimal("1"), environment=Environment.BACKTEST)
    quote = ExecutionQuote(
        instrument, quote_time, Decimal("99"), Decimal("100"), available_time=available_time
    )
    timeline = ExecutionTimeline.from_decision(quote_time, latency=timedelta(0)).with_fill_time(fill_time)

    with pytest.raises(ValueError, match="QUOTE_UNAVAILABLE_AT_FILL_TIME"):
        simulate_market_fill(order, quote, uuid4(), CostModel("v1"), timeline=timeline)
