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
    remaining_quantity: Decimal
    fill_sequence: int = 0
    last_fill_event_time: datetime | None = None


StrategyResult = Signal | Sequence[Signal] | None
StrategyFn = Callable[[PITMarketContext], StrategyResult]
RiskFn = Callable[[Signal], RiskAssessment]


class BacktestDataQualityError(ValueError):
    """Raised when market data fails the mandatory backtest safety gate."""

    def __init__(self, report: DataQualityReport) -> None:
        self.report = report
        super().__init__("BACKTEST_MARKET_DATA_QUALITY_UNSAFE")


class DeterministicBacktest:
    """Event-aware backtest with explicit PIT and future-quote execution boundaries.

    Signals are evaluated at their decision time. Approved orders may be delayed
    before submission, then remain pending until a later market event supplies an
    executable quote at or after the timeline's fill-eligibility time.
    Availability-only clock points are also processed so a delayed quote can
    execute when it actually becomes visible.

    ``max_fill_quantity`` is an explicit deterministic execution constraint. When
    set, each eligible quote fills at most that quantity; otherwise the full
    remaining order quantity is filled.
    """

    def __init__(
        self,
        initial_cash: Decimal,
        cost_model: CostModel,
        *,
        execution_latency: timedelta = timedelta(0),
        order_submission_delay: timedelta = timedelta(0),
        max_fill_quantity: Decimal | None = None,
    ) -> None:
        self._initial_cash = initial_cash
        self._ledger = PortfolioLedger(initial_cash)
        self._kernel = TradingKernel(self._ledger)
        self._cost_model = cost_model
        self._execution_latency = execution_latency
        self._order_submission_delay = order_submission_delay
        self._max_fill_quantity = max_fill_quantity
        if execution_latency < timedelta(0):
            raise ValueError("EXECUTION_LATENCY_MUST_BE_NON_NEGATIVE")
        if order_submission_delay < timedelta(0):
            raise ValueError("ORDER_SUBMISSION_DELAY_MUST_BE_NON_NEGATIVE")
        if max_fill_quantity is not None and max_fill_quantity <= 0:
            raise ValueError("MAX_FILL_QUANTITY_MUST_BE_POSITIVE")

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

        # A backtest run is an independent experiment. Reusing the same configured
        # DeterministicBacktest object must never carry portfolio or order lifecycle
        # state from a prior invocation into the next result.
        self._ledger = PortfolioLedger(self._initial_cash)
        self._kernel = TradingKernel(self._ledger)

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
            session_open = session_calendar is None or session_calendar.contains(current_time)
            for pending_order in pending:
                timeline = pending_order.timeline
                eligible_quotes = sorted(
                    (
                        visible_bar
                        for visible_bar in context.bars
                        if (
                            visible_bar.instrument_id == str(pending_order.signal.instrument_id)
                            and visible_bar.event_time > pending_order.signal.decision_time
                            and visible_bar.event_time >= timeline.fill_eligible_time
                            and (
                                pending_order.last_fill_event_time is None
                                or visible_bar.event_time > pending_order.last_fill_event_time
                            )
                            and visible_bar.available_time <= current_time
                            and (
                                session_calendar is None
                                or (
                                    session_calendar.contains(pending_order.signal.decision_time)
                                    and session_calendar.contains(timeline.order_time)
                                    and session_calendar.contains(visible_bar.event_time)
                                    and session_calendar.contains(visible_bar.available_time)
                                    and _same_session(
                                        session_calendar,
                                        pending_order.signal.decision_time,
                                        timeline.order_time,
                                    )
                                    and _same_session(
                                        session_calendar,
                                        pending_order.signal.decision_time,
                                        current_time,
                                    )
                                    and _same_session(
                                        session_calendar,
                                        visible_bar.available_time,
                                        current_time,
                                    )
                                )
                            )
                        )
                    ),
                    key=lambda candidate: (
                        candidate.event_time,
                        candidate.available_time,
                        candidate.ingestion_time,
                    ),
                )
                if eligible_quotes and session_open:
                    active_result = pending_order.result
                    remaining_quantity = pending_order.remaining_quantity
                    fill_sequence = pending_order.fill_sequence
                    last_fill_event_time = pending_order.last_fill_event_time
                    for quote_bar in eligible_quotes:
                        if remaining_quantity <= 0:
                            break
                        quote = ExecutionQuote(
                            instrument_id=UUID(quote_bar.instrument_id),
                            event_time=quote_bar.event_time,
                            bid=quote_bar.close,
                            ask=quote_bar.close,
                            available_time=quote_bar.available_time,
                        )
                        fill_quantity = remaining_quantity
                        if self._max_fill_quantity is not None:
                            fill_quantity = min(fill_quantity, self._max_fill_quantity)
                        execution = self._kernel.execute_order(
                            active_result.intent,
                            active_result.order_id,
                            quote,
                            self._cost_model,
                            decision_time=pending_order.signal.decision_time,
                            fill_id=uuid5(
                                NAMESPACE_URL,
                                f"hope:backtest:{pending_order.signal.signal_id}:fill:{fill_sequence}",
                            ),
                            timeline=timeline.with_fill_time(current_time),
                            quantity=fill_quantity,
                        )
                        complete_result = TradingKernelResult(
                            intent=execution.intent,
                            order_id=execution.order_id,
                            fill=execution.fill,
                            portfolio_state=execution.portfolio_state,
                            audit_events=active_result.audit_events + execution.audit_events,
                        )
                        valuation = value_portfolio(self._ledger, latest_marks, current_time)
                        events.append(BacktestEvent(current_time, quote_bar, complete_result, valuation))
                        remaining_quantity -= execution.fill.quantity
                        active_result = complete_result
                        fill_sequence += 1
                        last_fill_event_time = quote_bar.event_time
                    if remaining_quantity > 0:
                        remaining.append(_PendingOrder(
                            pending_order.signal,
                            active_result,
                            pending_order.timeline,
                            remaining_quantity,
                            fill_sequence,
                            last_fill_event_time,
                        ))
                else:
                    remaining.append(pending_order)
            pending = remaining

            if current_bars:
                current_instrument_ids: set[UUID] = set()
                for current_bar in current_bars:
                    try:
                        current_instrument_ids.add(UUID(current_bar.instrument_id))
                    except ValueError as exc:
                        raise ValueError("INVALID_BAR_INSTRUMENT_ID") from exc

                clock_signals: dict[UUID, Signal] = {}
                for signal in _normalize_strategy_signals(strategy(context)):
                    existing = clock_signals.get(signal.signal_id)
                    if existing is not None:
                        if existing != signal:
                            raise ValueError("SIGNAL_ID_REUSED_WITH_DIFFERENT_CONTENT")
                        raise ValueError("DUPLICATE_SIGNAL_ID")
                    clock_signals[signal.signal_id] = signal

                for signal in clock_signals.values():
                    if signal.signal_id in submitted_signal_ids:
                        raise ValueError("DUPLICATE_SIGNAL_ID")
                    if signal.instrument_id not in current_instrument_ids:
                        raise ValueError("SIGNAL_INSTRUMENT_NOT_UPDATED_AT_CONTEXT")
                    if signal.decision_time > context.as_of:
                        raise ValueError("SIGNAL_DECISION_AFTER_CONTEXT")
                    if signal.decision_time != context.as_of:
                        raise ValueError("SIGNAL_DECISION_MUST_MATCH_CONTEXT")
                    signal.assert_point_in_time(context.as_of)
                    submitted_signal_ids.add(signal.signal_id)

                    assessment = risk(signal)
                    timeline = ExecutionTimeline.from_decision(
                        signal.decision_time,
                        latency=self._execution_latency,
                        order_submission_delay=self._order_submission_delay,
                    )
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
                        timeline=timeline,
                    )
                    if submission.intent is not None:
                        pending.append(_PendingOrder(
                            signal,
                            submission,
                            timeline,
                            submission.intent.quantity,
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


def _normalize_strategy_signals(result: StrategyResult) -> tuple[Signal, ...]:
    if result is None:
        return ()
    if isinstance(result, Signal):
        return (result,)
    signals = tuple(result)
    if any(not isinstance(signal, Signal) for signal in signals):
        raise ValueError("STRATEGY_RETURNED_INVALID_SIGNAL_BATCH")
    return signals


def _same_session(
    calendar: MarketSessionCalendar,
    first: datetime,
    second: datetime,
) -> bool:
    return any(
        session_open <= first < session_close
        and session_open <= second < session_close
        for session_open, session_close in calendar.sessions
    )
