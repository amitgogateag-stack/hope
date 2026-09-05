from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timezone

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill


def make_order():
    return Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=uuid4(), side=OrderSide.BUY,
                 quantity=Decimal("10"), environment=Environment.PAPER)


def test_buy_fill_uses_ask_and_adds_slippage_and_commission():
    instrument = uuid4()
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=instrument, side=OrderSide.BUY,
                  quantity=Decimal("10"), environment=Environment.PAPER)
    quote = ExecutionQuote(instrument, object(), Decimal("99"), Decimal("100"))
    model = CostModel("cost-v1", Decimal("0.001"), Decimal("10"))

    fill = simulate_market_fill(order, quote, uuid4(), model)

    assert fill.price == Decimal("100.100")
    assert fill.commission == Decimal("1.001000")
    assert fill.slippage == Decimal("1.00")
    assert fill.cost_model_version == "cost-v1"


def test_sell_fill_uses_bid_and_applies_adverse_slippage():
    instrument = uuid4()
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=instrument, side=OrderSide.SELL,
                  quantity=Decimal("5"), environment=Environment.BACKTEST)
    quote = ExecutionQuote(instrument, object(), Decimal("100"), Decimal("101"))
    fill = simulate_market_fill(order, quote, uuid4(), CostModel("v1", Decimal("0"), Decimal("25")))
    assert fill.price == Decimal("99.75")


def test_quote_instrument_mismatch_is_rejected():
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=uuid4(), side=OrderSide.BUY,
                  quantity=Decimal("1"), environment=Environment.PAPER)
    quote = ExecutionQuote(uuid4(), object(), Decimal("1"), Decimal("1.01"))
    with pytest.raises(ValueError, match="ORDER_QUOTE_INSTRUMENT_MISMATCH"):
        simulate_market_fill(order, quote, uuid4(), CostModel("v1"))


def test_crossed_quote_is_rejected():
    with pytest.raises(ValueError, match="QUOTE_ASK_BELOW_BID"):
        ExecutionQuote(uuid4(), object(), Decimal("101"), Decimal("100"))


def test_simulator_supports_partial_fill_quantity():
    order = make_order()
    quote = ExecutionQuote(order.instrument_id, datetime.now(timezone.utc), Decimal("99"), Decimal("100"))
    fill = simulate_market_fill(order, quote, uuid4(), CostModel("test"), quantity=Decimal("4"))
    assert fill.quantity == Decimal("4")


def test_simulator_rejects_fill_quantity_above_order():
    order = make_order()
    quote = ExecutionQuote(order.instrument_id, datetime.now(timezone.utc), Decimal("99"), Decimal("100"))
    with pytest.raises(ValueError, match="FILL_QUANTITY_EXCEEDS_ORDER_QUANTITY"):
        simulate_market_fill(order, quote, uuid4(), CostModel("test"), quantity=Decimal("11"))
