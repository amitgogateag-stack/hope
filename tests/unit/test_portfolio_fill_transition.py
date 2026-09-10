from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from hope.domain.execution import CostModel, Environment, ExecutionQuote, Order, OrderSide, simulate_market_fill
from hope.domain.execution.timeline import ExecutionTimeline
from hope.domain.portfolio import PortfolioLedger


BASE_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)


def make_fill(instrument, side, qty, price, commission_rate="0", minute=0):
    order = Order(
        order_id=uuid4(),
        signal_id=uuid4(),
        instrument_id=instrument,
        side=side,
        quantity=Decimal(qty),
        environment=Environment.PAPER,
    )
    fill_time = BASE_TIME + timedelta(minutes=minute)
    quote = ExecutionQuote(instrument, fill_time, Decimal(price), Decimal(price))
    timeline = ExecutionTimeline.from_decision(fill_time, latency=timedelta(0))
    return simulate_market_fill(
        order,
        quote,
        uuid4(),
        CostModel("transition-test", Decimal(commission_rate), Decimal("0")),
        timeline=timeline,
    )


def test_fill_transition_reports_authoritative_accounting_deltas():
    instrument = uuid4()
    ledger = PortfolioLedger(Decimal("10000"))

    buy = ledger.apply_fill_with_transition(
        make_fill(instrument, OrderSide.BUY, "10", "100", "0.001")
    )
    assert buy.realized_pnl_delta == Decimal("0")
    assert buy.commission_delta == Decimal("1.000")
    assert buy.cash_delta == Decimal("-1001.000")
    assert buy.state_before.cash == Decimal("10000")
    assert buy.state_after.cash == Decimal("8999.000")

    sell = ledger.apply_fill_with_transition(
        make_fill(instrument, OrderSide.SELL, "10", "110", "0.001", minute=1)
    )
    assert sell.realized_pnl_delta == Decimal("100")
    assert sell.commission_delta == Decimal("1.100")
    assert sell.cash_delta == Decimal("1098.900")
    assert sell.state_before == buy.state_after
    assert sell.state_after == ledger.state
    assert sell.state_after.positions[instrument].realized_pnl == Decimal("100")
