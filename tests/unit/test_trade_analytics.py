from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.backtests.trades import calculate_trade_metrics, reconstruct_completed_trades
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import Fill


def make_fill(side, qty, price, commission="0", slippage="0"):
    return Fill(uuid4(), uuid4(), uuid4(), uuid4(), side, Decimal(qty), Decimal(price), Decimal(commission), Decimal(slippage), "cost-v1")


def test_long_round_trip_metrics():
    instrument = uuid4()
    order1, order2, signal = uuid4(), uuid4(), uuid4()
    buy = Fill(uuid4(), order1, signal, instrument, OrderSide.BUY, Decimal("10"), Decimal("100"), Decimal("1"), Decimal("0.50"), "cost-v1")
    sell = Fill(uuid4(), order2, signal, instrument, OrderSide.SELL, Decimal("10"), Decimal("110"), Decimal("1"), Decimal("0.60"), "cost-v1")
    # Fill has no timestamp today; trade reconstruction therefore uses supplied order only.
    metrics = calculate_trade_metrics((buy, sell))
    assert metrics.completed_trades == 1
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 0
    assert metrics.gross_profit == Decimal("100")
    assert metrics.total_commission == Decimal("2")
    assert metrics.total_slippage == Decimal("1.10")
    assert metrics.turnover == Decimal("2100")


def test_partial_close_reconstructs_completed_trade():
    instrument = uuid4()
    signal = uuid4()
    fills = (
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.BUY, Decimal("10"), Decimal("100"), Decimal("0"), Decimal("0"), "v1"),
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.SELL, Decimal("4"), Decimal("110"), Decimal("0"), Decimal("0"), "v1"),
    )
    trades = reconstruct_completed_trades(fills)
    assert len(trades) == 1
    assert trades[0].quantity == Decimal("4")
    assert trades[0].gross_pnl == Decimal("40")


def test_losing_trade_and_profit_factor_use_gross_pnl():
    instrument = uuid4()
    signal = uuid4()
    fills = (
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.BUY, Decimal("10"), Decimal("100"), Decimal("1"), Decimal("0"), "v1"),
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.SELL, Decimal("10"), Decimal("90"), Decimal("1"), Decimal("0"), "v1"),
    )
    metrics = calculate_trade_metrics(fills)
    assert metrics.losing_trades == 1
    assert metrics.gross_loss == Decimal("100")
    assert metrics.profit_factor == Decimal("0")
    assert metrics.expectancy == Decimal("-102")


def test_duplicate_fill_is_rejected():
    fill = make_fill(OrderSide.BUY, "1", "100")
    with pytest.raises(ValueError, match="DUPLICATE_FILL"):
        reconstruct_completed_trades((fill, fill))


def test_short_round_trip_reconstructs_completed_trade():
    instrument = uuid4()
    signal = uuid4()
    fills = (
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.SELL, Decimal("10"), Decimal("100"), Decimal("1"), Decimal("0"), "v1"),
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.BUY, Decimal("10"), Decimal("90"), Decimal("1"), Decimal("0"), "v1"),
    )
    trades = reconstruct_completed_trades(fills)
    assert len(trades) == 1
    assert trades[0].quantity == Decimal("10")
    assert trades[0].gross_pnl == Decimal("100")
    assert trades[0].net_pnl == Decimal("98")


def test_long_to_short_reversal_reconstructs_both_legs():
    instrument = uuid4()
    signal = uuid4()
    fills = (
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.BUY, Decimal("10"), Decimal("100"), Decimal("0"), Decimal("0"), "v1"),
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.SELL, Decimal("15"), Decimal("110"), Decimal("0"), Decimal("0"), "v1"),
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.BUY, Decimal("5"), Decimal("90"), Decimal("0"), Decimal("0"), "v1"),
    )
    trades = reconstruct_completed_trades(fills)
    assert len(trades) == 2
    assert trades[0].quantity == Decimal("10")
    assert trades[0].gross_pnl == Decimal("100")
    assert trades[1].quantity == Decimal("5")
    assert trades[1].gross_pnl == Decimal("100")


def test_short_to_long_reversal_reconstructs_both_legs():
    instrument = uuid4()
    signal = uuid4()
    fills = (
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.SELL, Decimal("10"), Decimal("100"), Decimal("0"), Decimal("0"), "v1"),
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.BUY, Decimal("15"), Decimal("90"), Decimal("0"), Decimal("0"), "v1"),
        Fill(uuid4(), uuid4(), signal, instrument, OrderSide.SELL, Decimal("5"), Decimal("80"), Decimal("0"), Decimal("0"), "v1"),
    )
    trades = reconstruct_completed_trades(fills)
    assert len(trades) == 2
    assert trades[0].quantity == Decimal("10")
    assert trades[0].gross_pnl == Decimal("100")
    assert trades[1].quantity == Decimal("5")
    assert trades[1].gross_pnl == Decimal("-50")
