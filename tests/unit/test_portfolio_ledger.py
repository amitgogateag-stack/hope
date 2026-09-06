import pytest
from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def make_fill(instrument, side, qty, price, commission="0", minute=0):
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=instrument, side=side, quantity=Decimal(qty), environment=Environment.PAPER)
    fill_time = BASE_TIME + timedelta(minutes=minute)
    quote = ExecutionQuote(instrument, fill_time, Decimal(price), Decimal(price))
    timeline = ExecutionTimeline.from_decision(fill_time, latency=timedelta(0))
    return simulate_market_fill(order, quote, uuid4(), CostModel("test", Decimal(commission), Decimal("0")), timeline=timeline)


def test_buy_then_sell_realizes_pnl_and_tracks_cash():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(make_fill(instrument, OrderSide.BUY, "10", "100"))
    state = ledger.apply_fill(make_fill(instrument, OrderSide.SELL, "10", "110", minute=1))

    position = state.positions[instrument]
    assert position.quantity == Decimal("0")
    assert position.average_price == Decimal("0")
    assert position.realized_pnl == Decimal("100")
    assert state.cash == Decimal("10100")


def test_partial_sell_realizes_only_closed_quantity():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(make_fill(instrument, OrderSide.BUY, "10", "100"))
    state = ledger.apply_fill(make_fill(instrument, OrderSide.SELL, "4", "110", minute=1))

    position = state.positions[instrument]
    assert position.quantity == Decimal("6")
    assert position.average_price == Decimal("100")
    assert position.realized_pnl == Decimal("40")


def test_short_then_cover_realizes_pnl():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(make_fill(instrument, OrderSide.SELL, "10", "100"))
    state = ledger.apply_fill(make_fill(instrument, OrderSide.BUY, "10", "90", minute=1))

    assert state.positions[instrument].quantity == Decimal("0")
    assert state.positions[instrument].realized_pnl == Decimal("100")
    assert state.cash == Decimal("10100")


def test_reversal_opens_excess_at_new_fill_price():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(make_fill(instrument, OrderSide.BUY, "10", "100"))
    state = ledger.apply_fill(make_fill(instrument, OrderSide.SELL, "15", "110", minute=1))

    position = state.positions[instrument]
    assert position.quantity == Decimal("-5")
    assert position.average_price == Decimal("110")
    assert position.realized_pnl == Decimal("100")


def test_commission_reduces_cash_and_is_tracked():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    state = ledger.apply_fill(make_fill(instrument, OrderSide.BUY, "10", "100", "0.001"))

    assert state.cash == Decimal("8999.000")
    assert state.positions[instrument].total_commission == Decimal("1.000")


def test_unrealized_pnl_uses_signed_position():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(make_fill(instrument, OrderSide.BUY, "10", "100"))
    assert ledger.unrealized_pnl({instrument: Decimal("105")}) == Decimal("50")


def test_missing_mark_is_rejected():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(make_fill(instrument, OrderSide.BUY, "10", "100"))
    try:
        ledger.unrealized_pnl({})
    except KeyError as exc:
        assert str(exc).startswith("'MISSING_MARK:")
    else:
        raise AssertionError("expected missing mark failure")


def test_duplicate_fill_is_rejected():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    fill = make_fill(instrument, OrderSide.BUY, "10", "100")
    ledger.apply_fill(fill)
    with pytest.raises(ValueError, match="DUPLICATE_FILL"):
        ledger.apply_fill(fill)
