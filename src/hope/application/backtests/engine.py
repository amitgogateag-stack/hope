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
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.application.market_data.quality import DataQualityReport, validate_bars
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
    unfilled_order_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class _PendingOrder:
    signal: Signal
    result: TradingKernelResult
    timeline: ExecutionTimeline


StrategyFn = Callable[[PITMarketContext], Signal | None]
RiskFn = Callable[[Signal], RiskAssessment]


class BacktestDataQualityError(ValueError):
    """Raised when market data fails the mandatory backtest safety gate."""

    def __init__(self, report: DataQualityReport) -> None:
        self.report = report
        super().__init__("BACKTEST_MARKET_DATA_QUALITY_UNSAFE")


class DeterministicBacktest:
    """Event-aware backtest with explicit PIT and future-quote execution boundaries.

    Signals are submitted at their decision time. Approved orders remain pending
    until a later market event supplies an executable quote at or after the
    timeline's fill-eligibility time. Availability-only clock points are also
    processed so a delayed quote can execute when it actually becomes visible.
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
        *,
        expected_latest_event_time: datetime | None = None,
        expected_instrument_ids: tuple[str, ...] | None = None,
        expected_interval: timedelta | None = None,
        session_calendar: MarketSessionCalendar | None = None,
    ) -> BacktestResult:
        ordered: Sequence[MarketBar] = tuple(bars)
        quality = validate_bars(
            ordered,
            expected_latest_event_time=expected_latest_event_time,
            expected_instrument_ids=expected_instrument_ids,
            expected_interval=expected_interval,
            session_calendar=session_calendar,
        )
        if not quality.safe:
            raise BacktestDataQualityError(quality)

        previous_time: datetime | None = None
        events: list[BacktestEvent] = []
        valuations: list[PortfolioValuation] = []
        latest_marks = {}
        pending: list[_PendingOrder] = []
        submitted_signal_ids: set[UUID] = set()

        event_times = {bar.event_time for bar in ordered}
        clock_times = sorted(event_times | {bar.available_time for bar in ordered})

        for current_time in clock_times:
            current_bars = tuple(
                sorted(
                    (bar for bar in ordered if bar.event_time == current_time),
                    key=lambda bar: (bar.instrument_id, bar.available_time, bar.ingestion_time),
                )
            )
            if current_bars:
                if previous_time is not None and current_time < previous_time:
                    raise ValueError("BACKTEST_EVENTS_MUST_BE_NON_DECREASING")
                previous_time = current_time

            context = build_pit_market_context(tuple(ordered), current_time)
            for visible_bar in context.bars:
                latest_marks[UUID(visible_bar.instrument_id)] = visible_bar.close

            remaining: list[_PendingOrder] = []
            for pending_order in pending:
                timeline = pending_order.timeline
                eligible_quotes = [
                    visible_bar
                    for visible_bar in context.bars
                    if (
                        visible_bar.instrument_id == str(pending_order.signal.instrument_id)
                        and visible_bar.event_time > pending_order.signal.decision_time
                        and visible_bar.event_time >= timeline.fill_eligible_time
                        and visible_bar.available_time <= current_time
                    )
                ]
                quote_bar = max(eligible_quotes, key=lambda candidate: candidate.event_time, default=None)
                if quote_bar is not None:
                    quote = ExecutionQuote(
                        instrument_id=UUID(quote_bar.instrument_id),
                        event_time=quote_bar.event_time,
                        bid=quote_bar.close,
                        ask=quote_bar.close,
                        available_time=quote_bar.available_time,
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
                        timeline=timeline.with_fill_time(current_time),
                    )
                    complete_result = TradingKernelResult(
                        intent=execution.intent,
                        order_id=execution.order_id,
                        fill=execution.fill,
                        portfolio_state=execution.portfolio_state,
                        audit_events=pending_order.result.audit_events + execution.audit_events,
                    )
                    valuation = value_portfolio(self._ledger, latest_marks, current_time)
                    event_bar = current_bars[0] if current_bars else quote_bar
                    events.append(BacktestEvent(current_time, event_bar, complete_result, valuation))
                else:
                    remaining.append(pending_order)
            pending = remaining

            for current_bar in current_bars:
                signal = strategy(context)
                if signal is not None:
                    if signal.signal_id in submitted_signal_ids:
                        raise ValueError("DUPLICATE_SIGNAL_ID")
                    try:
                        bar_instrument_id = UUID(current_bar.instrument_id)
                    except ValueError as exc:
                        raise ValueError("INVALID_BAR_INSTRUMENT_ID") from exc
                    if signal.instrument_id != bar_instrument_id:
                        raise ValueError("SIGNAL_BAR_INSTRUMENT_MISMATCH")
                    if signal.decision_time > context.as_of:
                        raise ValueError("SIGNAL_DECISION_AFTER_CONTEXT")
                    if signal.decision_time != context.as_of:
                        raise ValueError("SIGNAL_DECISION_MUST_MATCH_CONTEXT")
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
                        submitted_signal_ids.add(signal.signal_id)
                        pending.append(_PendingOrder(
                            signal,
                            submission,
                            ExecutionTimeline.from_decision(
                                signal.decision_time,
                                latency=self._execution_latency,
                            ),
                        ))

                valuations.append(value_portfolio(self._ledger, latest_marks, current_time))

        return BacktestResult(
            self._ledger.initial_cash,
            self._ledger.state,
            tuple(events),
            tuple(valuations),
            calculate_metrics(tuple(valuations)),
            tuple(item.result.order_id for item in pending),
        )
