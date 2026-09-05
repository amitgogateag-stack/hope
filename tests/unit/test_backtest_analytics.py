from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hope.application.backtests.analytics import calculate_metrics
from hope.domain.portfolio.valuation import PortfolioValuation


def valuation(t, equity):
    equity = Decimal(str(equity))
    return PortfolioValuation(
        as_of=t,
        cash=equity,
        market_value=Decimal("0"),
        realized_pnl=equity - Decimal("1000"),
        unrealized_pnl=Decimal("0"),
        commissions=Decimal("0"),
        equity=equity,
        total_pnl=equity - Decimal("1000"),
    )


def test_metrics_total_return_and_drawdown():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    vals = [valuation(t, 1000), valuation(t + timedelta(days=1), 1100), valuation(t + timedelta(days=2), 990), valuation(t + timedelta(days=3), 1089)]
    metrics = calculate_metrics(vals)
    assert metrics.total_return == Decimal("0.089")
    assert metrics.max_drawdown == Decimal("0.1")
    assert metrics.total_pnl == Decimal("89")


def test_metrics_are_not_annualized_without_explicit_frequency():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    vals = [valuation(t, 100), valuation(t + timedelta(days=1), 110)]
    metrics = calculate_metrics(vals)
    assert metrics.volatility == Decimal("0")
    assert metrics.sharpe == Decimal("0")
    assert metrics.total_return == Decimal("0.1")


def test_metrics_can_explicitly_annualize_volatility():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    vals = [valuation(t, 100), valuation(t + timedelta(days=1), 110), valuation(t + timedelta(days=2), 99)]
    metrics = calculate_metrics(vals, periods_per_year=4)
    assert metrics.volatility > Decimal("0")


def test_metrics_reject_non_monotonic_timestamps():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="VALUATIONS_MUST_BE_NON_DECREASING"):
        calculate_metrics([valuation(t + timedelta(days=1), 100), valuation(t, 101)])


def test_metrics_reject_non_positive_initial_equity():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="INITIAL_EQUITY_MUST_BE_POSITIVE"):
        calculate_metrics([valuation(t, 0)])
