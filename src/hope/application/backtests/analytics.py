from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from typing import Sequence

from hope.domain.portfolio.valuation import PortfolioValuation


@dataclass(frozen=True)
class BacktestMetrics:
    initial_equity: Decimal
    final_equity: Decimal
    total_pnl: Decimal
    total_return: Decimal
    max_drawdown: Decimal
    volatility: Decimal
    sharpe: Decimal
    downside_deviation: Decimal
    sortino: Decimal


def _returns(valuations: Sequence[PortfolioValuation]) -> list[Decimal]:
    if not valuations:
        raise ValueError("VALUATIONS_REQUIRED")
    if valuations[0].equity <= 0:
        raise ValueError("INITIAL_EQUITY_MUST_BE_POSITIVE")
    result: list[Decimal] = []
    previous = valuations[0].equity
    for current in valuations[1:]:
        if current.equity <= 0:
            raise ValueError("EQUITY_MUST_BE_POSITIVE")
        result.append((current.equity - previous) / previous)
        previous = current.equity
    return result


def _population_std(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum(((value - mean) ** 2 for value in values), Decimal("0")) / Decimal(len(values))
    return Decimal(str(sqrt(float(variance))))


def calculate_metrics(
    valuations: Sequence[PortfolioValuation],
    *,
    periods_per_year: int | None = None,
    risk_free_rate_per_period: Decimal = Decimal("0"),
) -> BacktestMetrics:
    """Calculate deterministic performance metrics from an equity timeline.

    No annualization is performed unless ``periods_per_year`` is supplied. This
    avoids silently assuming that bars are daily, hourly, or otherwise regular.
    """
    if not valuations:
        raise ValueError("VALUATIONS_REQUIRED")
    for previous, current in zip(valuations, valuations[1:]):
        if current.as_of < previous.as_of:
            raise ValueError("VALUATIONS_MUST_BE_NON_DECREASING")

    initial = valuations[0].equity
    final = valuations[-1].equity
    if initial <= 0:
        raise ValueError("INITIAL_EQUITY_MUST_BE_POSITIVE")

    returns = _returns(valuations)
    total_return = (final - initial) / initial

    peak = initial
    max_drawdown = Decimal("0")
    for valuation in valuations:
        if valuation.equity > peak:
            peak = valuation.equity
        drawdown = (peak - valuation.equity) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    excess = [value - risk_free_rate_per_period for value in returns]
    volatility = _population_std(returns)
    downside = [min(value - risk_free_rate_per_period, Decimal("0")) for value in returns]
    downside_deviation = _population_std(downside)

    mean_excess = sum(excess, Decimal("0")) / Decimal(len(excess)) if excess else Decimal("0")
    sharpe = Decimal("0") if volatility == 0 else mean_excess / volatility
    sortino = Decimal("0") if downside_deviation == 0 else mean_excess / downside_deviation

    if periods_per_year is not None:
        if periods_per_year <= 0:
            raise ValueError("PERIODS_PER_YEAR_MUST_BE_POSITIVE")
        scale = Decimal(str(sqrt(periods_per_year)))
        volatility *= scale
        sharpe *= scale
        sortino *= scale
        downside_deviation *= scale

    return BacktestMetrics(
        initial_equity=initial,
        final_equity=final,
        total_pnl=final - initial,
        total_return=total_return,
        max_drawdown=max_drawdown,
        volatility=volatility,
        sharpe=sharpe,
        downside_deviation=downside_deviation,
        sortino=sortino,
    )
