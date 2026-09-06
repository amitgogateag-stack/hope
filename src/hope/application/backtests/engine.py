from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Iterable, Sequence
from uuid import UUID, NAMESPACE_URL, uuid5

from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.simulator import CostModel, ExecutionQuote
from hope.domain.market_data.context import PITMarketContext, build_pit_market_context
from hope.domain.market_data.models import MarketBar
from hope.domain.portfolio.ledger import PortfolioLedger, PortfolioState
from hope.domain.portfolio.valuation import PortfolioValuation, value_portfolio
from hope.domain.risk.models import RiskAssessment
from hope.domain.signal.models import Signal
from hope.application.trading.service import TradingKernel, TradingKernelResult
from hope.application.backtests.analytics import BacktestMetrics, calculate_metrics


@dataclass(frozen=True)
class BacktestEvent:
    event_time: datetime
    bar: MarketBar
    result: TradingKernelResult
    valuation: PortfolioValuation


@dataclass(frozen=True)
class BacktestResult:
    initial_cash: Decimal
    final_state: PortfolioState
    events: tuple[BacktestEvent, ...]
    valuations: tuple[PortfolioValuation, ...]
    metrics: BacktestMetrics


StrategyFn = Callable[[PITMarketContext], Signal | None]
RiskFn = Callable[[Signal], RiskAssessment]


class DeterministicBacktest:
    """Small, event-aware backtest core with an explicit PIT strategy boundary.

    Strategies receive only a point-in-time market context. Execution remains
    intentionally simple in this phase; P0-B will introduce the full order,
    latency, eligibility, and fill timeline.
    """

    def __init__(self, initial_cash: Decimal, cost_model: CostModel) -> None:
        self._ledger = PortfolioLedger(initial_cash)
        self._kernel = TradingKernel(self._ledger)
        self._cost_model = cost_model

    def run(
        self,
        bars: Iterable[MarketBar],
        strategy: StrategyFn,
        risk: RiskFn,
        side: OrderSide,
    ) -> BacktestResult:
        ordered: Sequence[MarketBar] = tuple(bars)
        previous_time: datetime | None = None
        events: list[BacktestEvent] = []
        valuations: list[PortfolioValuation] = []
        latest_marks = {}

        for bar in ordered:
            if previous_time is not None and bar.event_time < previous_time:
                raise ValueError("BACKTEST_EVENTS_MUST_BE_NON_DECREASING")
            previous_time = bar.event_time

            try:
                bar_instrument_id = UUID(bar.instrument_id)
            except ValueError as exc:
                raise ValueError("INVALID_BAR_INSTRUMENT_ID") from exc
            latest_marks[bar_instrument_id] = bar.close

            context = build_pit_market_context(tuple(ordered), bar.available_time)
            signal = strategy(context)
            if signal is not None:
                if signal.instrument_id != bar_instrument_id:
                    raise ValueError("SIGNAL_BAR_INSTRUMENT_MISMATCH")
                signal.assert_point_in_time(context.as_of)

                assessment = risk(signal)
                quote = ExecutionQuote(
                    instrument_id=signal.instrument_id,
                    event_time=bar.event_time,
                    bid=bar.close,
                    ask=bar.close,
                )
                result = self._kernel.process(
                    signal,
                    assessment,
                    side,
                    Environment.BACKTEST,
                    quote,
                    self._cost_model,
                    order_id=uuid5(NAMESPACE_URL, f"hope:backtest:{signal.signal_id}:order"),
                    fill_id=uuid5(NAMESPACE_URL, f"hope:backtest:{signal.signal_id}:fill"),
                )
                valuation = value_portfolio(self._ledger, latest_marks, bar.event_time)
                events.append(BacktestEvent(bar.event_time, bar, result, valuation))
            else:
                valuation = value_portfolio(self._ledger, latest_marks, bar.event_time)
            valuations.append(valuation)

        return BacktestResult(
            self._ledger.initial_cash,
            self._ledger.state,
            tuple(events),
            tuple(valuations),
            calculate_metrics(tuple(valuations)),
        )
