from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio.ledger import PortfolioLedger
from hope.domain.portfolio.valuation import value_portfolio


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def make_fill(instrument, side, qty, price, commission="0"):
    order = Order(order_id=uuid4(), signal_id=uuid4(), instrument_id=instrument,
                  side=side, quantity=Decimal(qty), environment=Environment.PAPER)
    quote = ExecutionQuote(instrument, BASE_TIME, Decimal(price), Decimal(price))
    timeline = ExecutionTimeline.from_decision(BASE_TIME, latency=timedelta(0))
    return simulate_market_fill(order, quote, uuid4(), CostModel("test", Decimal(commission), Decimal("0")), timeline=timeline)


def test_valuation_reconciles_open_long_pnl_and_commission():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(make_fill(instrument, OrderSide.BUY, "10", "100", "0.001"))

    v = value_portfolio(ledger, {instrument: Decimal("105")}, BASE_TIME + timedelta(minutes=1))

    assert v.cash == Decimal("8999")
    assert v.market_value == Decimal("1050")
    assert v.unrealized_pnl == Decimal("50")
    assert v.commissions == Decimal("1")
    assert v.equity == Decimal("10049")
    assert v.total_pnl == Decimal("49")
    assert v.total_pnl == v.realized_pnl + v.unrealized_pnl - v.commissions


def test_valuation_reconciles_short_position():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(make_fill(instrument, OrderSide.SELL, "10", "100"))

    v = value_portfolio(ledger, {instrument: Decimal("90")}, BASE_TIME + timedelta(minutes=1))

    assert v.market_value == Decimal("-900")
    assert v.unrealized_pnl == Decimal("100")
    assert v.equity == Decimal("10100")
    assert v.total_pnl == Decimal("100")


def test_valuation_requires_timezone_aware_time():
    with pytest.raises(ValueError, match="VALUATION_TIME_MUST_BE_TIMEZONE_AWARE"):
        value_portfolio(PortfolioLedger(Decimal("10000")), {}, datetime(2026, 8, 29, 12, 0))


def test_valuation_requires_marks_for_open_positions():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))
    ledger.apply_fill(make_fill(instrument, OrderSide.BUY, "1", "100"))

    with pytest.raises(KeyError, match="MISSING_MARK"):
        value_portfolio(ledger, {}, BASE_TIME + timedelta(minutes=1))
