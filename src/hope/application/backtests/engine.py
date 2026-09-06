from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Iterable, Sequence
from uuid import UUID, NAMESPACE_URL, uuid5

from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.simulator import CostModel, ExecutionQuote
from hope.domain.execution.timeline import ExecutionTimeline
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


@dataclass(frozen=True)
class _PendingOrder:
    signal: Signal
    result: TradingKernelResult
    timeline: ExecutionTimeline


StrategyFn = Callable[[PITMarketContext], Signal | None]
RiskFn = Callable[[Signal], RiskAssessment]


class DeterministicBacktest:
    """Event-aware backtest with explicit PIT and future-quote execution boundaries.

    Signals are submitted at their decision time. Approved orders remain pending
    until a later market event supplies an executable quote at or after the
    timeline's fill-eligibility time. This deliberately prevents a strategy from
    filling against the same market bar that generated its own decision.
    """

    def __init__(
        self,
        initial_cash: Decimal,
        cost_model: CostModel,
        *,
        execution_latency: timedelta = timedelta(0),
    ) -> None:
        self._ledger = PortfolioLedger(initial_cash)
        self._kernel = TradingKernel(self._ledger)
        self._cost_model = cost_model
        self._execution_latency = execution_latency
        if execution_latency < timedelta(0):
            raise ValueError("EXECUTION_LATENCY_MUST_BE_NON_NEGATIVE")

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
        pending: list[_PendingOrder] = []

        for bar in ordered:
            if previous_time is not None and bar.event_time < previous_time:
                raise ValueError("BACKTEST_EVENTS_MUST_BE_NON_DECREASING")
            previous_time = bar.event_time

            try:
                bar_instrument_id = UUID(bar.instrument_id)
            except ValueError as exc:
                raise ValueError("INVALID_BAR_INSTRUMENT_ID") from exc
            latest_marks[bar_instrument_id] = bar.close

            remaining: list[_PendingOrder] = []
            for pending_order in pending:
                timeline = pending_order.timeline
                if (
                    bar.instrument_id == str(pending_order.signal.instrument_id)
                    and bar.event_time > pending_order.signal.decision_time
                    and bar.event_time >= timeline.fill_eligible_time
                ):
                    quote = ExecutionQuote(
                        instrument_id=bar_instrument_id,
                        event_time=bar.event_time,
                        bid=bar.close,
                        ask=bar.close,
                    )
                    execution = self._kernel.execute_order(
                        pending_order.result.intent,
                        pending_order.result.order_id,
                        quote,
                        self._cost_model,
                        decision_time=pending_order.signal.decision_time,
                        fill_id=uuid5(
                            NAMESPACE_URL,
                            f"hope:backtest:{pending_order.signal.signal_id}:fill",
                        ),
                        timeline=timeline.with_fill_time(bar.event_time),
                    )
                    valuation = value_portfolio(self._ledger, latest_marks, bar.event_time)
                    events.append(BacktestEvent(bar.event_time, bar, execution, valuation))
                else:
                    remaining.append(pending_order)
            pending = remaining

            context = build_pit_market_context(tuple(ordered), bar.available_time)
            signal = strategy(context)
            if signal is not None:
                if signal.instrument_id != bar_instrument_id:
                    raise ValueError("SIGNAL_BAR_INSTRUMENT_MISMATCH")
                signal.assert_point_in_time(context.as_of)

                assessment = risk(signal)
                submission = self._kernel.process(
                    signal,
                    assessment,
                    side,
                    Environment.BACKTEST,
                    None,
                    None,
                    order_id=uuid5(
                        NAMESPACE_URL,
                        f"hope:backtest:{signal.signal_id}:order",
                    ),
                )
                if submission.intent is not None:
                    pending.append(_PendingOrder(
                        signal,
                        submission,
                        ExecutionTimeline.from_decision(
                            signal.decision_time,
                            latency=self._execution_latency,
                        ),
                    ))

            valuations.append(value_portfolio(self._ledger, latest_marks, bar.event_time))

        return BacktestResult(
            self._ledger.initial_cash,
            self._ledger.state,
            tuple(events),
            tuple(valuations),
            calculate_metrics(tuple(valuations)),
        )
