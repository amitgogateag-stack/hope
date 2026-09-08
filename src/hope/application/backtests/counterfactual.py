from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable
from uuid import UUID, NAMESPACE_URL, uuid5

from hope.application.backtests.engine import BacktestDecision, BacktestResult, DeterministicBacktest
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import CostModel
from hope.domain.market_data.models import MarketBar
from hope.domain.portfolio.ledger import PortfolioLedger
from hope.domain.portfolio.valuation import PortfolioValuation, value_portfolio
from hope.domain.research.counterfactual import CounterfactualAcceptancePolicy
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


@dataclass(frozen=True)
class CounterfactualHorizonValuation:
    """Exact-horizon diagnostic value using the latest PIT-visible instrument mark."""

    mark_bar: MarketBar
    valuation: PortfolioValuation


@dataclass(frozen=True)
class CounterfactualAcceptanceReplay:
    """Isolated execution evidence for accepting one actually rejected ENTRY signal."""

    source_signal_id: UUID
    counterfactual_signal_id: UUID
    source_risk: RiskAssessment
    policy: CounterfactualAcceptancePolicy
    horizon: datetime
    entry_side: OrderSide
    execution_latency: timedelta
    order_submission_delay: timedelta
    max_fill_quantity: Decimal | None
    cost_model_version: str
    horizon_valuation: CounterfactualHorizonValuation
    replay: BacktestResult


def replay_rejected_entry_acceptance(
    source_decision: BacktestDecision,
    bars: Iterable[MarketBar],
    policy: CounterfactualAcceptancePolicy,
    side: OrderSide,
    initial_cash: Decimal,
    cost_model: CostModel,
    *,
    execution_latency: timedelta = timedelta(0),
    order_submission_delay: timedelta = timedelta(0),
    max_fill_quantity: Decimal | None = None,
    session_calendar: MarketSessionCalendar | None = None,
) -> CounterfactualAcceptanceReplay:
    """Replay one risk-rejected ENTRY on an isolated normal backtest engine.

    The hypothetical acceptance uses normal backtest execution semantics through
    the declared horizon. MARK_TO_HORIZON is represented by explicit valuation
    evidence at the horizon; no synthetic strategy EXIT is created.
    """

    signal = source_decision.signal
    if signal.signal_type is not SignalType.ENTRY:
        raise ValueError("COUNTERFACTUAL_REQUIRES_REJECTED_ENTRY_SIGNAL")
    if source_decision.risk.decision is not RiskDecision.REJECT:
        raise ValueError("COUNTERFACTUAL_REQUIRES_RISK_REJECTION")
    if source_decision.risk.signal_id != signal.signal_id:
        raise ValueError("COUNTERFACTUAL_SOURCE_RISK_SIGNAL_MISMATCH")
    if source_decision.result.intent is not None or source_decision.result.order_id is not None:
        raise ValueError("COUNTERFACTUAL_SOURCE_DECISION_WAS_EXECUTABLE")
    if source_decision.decision_time != signal.decision_time:
        raise ValueError("COUNTERFACTUAL_SOURCE_DECISION_TIME_MISMATCH")
    if initial_cash <= 0:
        raise ValueError("COUNTERFACTUAL_INITIAL_CASH_MUST_BE_POSITIVE")

    horizon = signal.decision_time + policy.holding_period
    source_bars = tuple(bars)
    bounded_bars = tuple(
        bar
        for bar in source_bars
        if bar.event_time <= horizon and bar.available_time <= horizon
    )
    if not any(
        bar.event_time == signal.decision_time
        and bar.instrument_id == str(signal.instrument_id)
        for bar in bounded_bars
    ):
        raise ValueError("COUNTERFACTUAL_SOURCE_BAR_REQUIRED")

    counterfactual_signal_id = _counterfactual_signal_id(signal, policy)
    counterfactual_signal = signal.model_copy(
        update={"signal_id": counterfactual_signal_id}
    )

    def strategy(context):
        if context.as_of == counterfactual_signal.decision_time:
            return counterfactual_signal
        return None

    def accept(_signal: Signal) -> RiskAssessment:
        if _signal.signal_id != counterfactual_signal_id:
            raise ValueError("COUNTERFACTUAL_UNEXPECTED_SIGNAL")
        return RiskAssessment(
            signal_id=counterfactual_signal_id,
            decision=RiskDecision.APPROVE,
            reason_code=f"COUNTERFACTUAL_ACCEPT:{policy.policy_id}:{policy.version}",
            approved_quantity=policy.quantity,
        )

    replay = DeterministicBacktest(
        initial_cash,
        cost_model,
        execution_latency=execution_latency,
        order_submission_delay=order_submission_delay,
        max_fill_quantity=max_fill_quantity,
    ).run(
        bounded_bars,
        strategy,
        accept,
        side,
        session_calendar=session_calendar,
    )
    if len(replay.decisions) != 1:
        raise ValueError("COUNTERFACTUAL_DECISION_NOT_REPLAYED")

    horizon_valuation = _mark_replay_to_horizon(
        replay,
        bounded_bars,
        signal.instrument_id,
        horizon,
        initial_cash,
    )

    return CounterfactualAcceptanceReplay(
        source_signal_id=signal.signal_id,
        counterfactual_signal_id=counterfactual_signal_id,
        source_risk=source_decision.risk,
        policy=policy,
        horizon=horizon,
        entry_side=side,
        execution_latency=execution_latency,
        order_submission_delay=order_submission_delay,
        max_fill_quantity=max_fill_quantity,
        cost_model_version=cost_model.version,
        horizon_valuation=horizon_valuation,
        replay=replay,
    )


def _mark_replay_to_horizon(
    replay: BacktestResult,
    bounded_bars: tuple[MarketBar, ...],
    instrument_id: UUID,
    horizon: datetime,
    initial_cash: Decimal,
) -> CounterfactualHorizonValuation:
    visible_marks = tuple(
        bar
        for bar in bounded_bars
        if bar.instrument_id == str(instrument_id)
    )
    if not visible_marks:
        raise ValueError("COUNTERFACTUAL_HORIZON_MARK_REQUIRED")
    mark_bar = max(
        visible_marks,
        key=lambda bar: (bar.event_time, bar.available_time, bar.ingestion_time),
    )

    ledger = PortfolioLedger(initial_cash)
    for event in replay.events:
        if event.result.fill is not None:
            ledger.apply_fill(event.result.fill)
    if ledger.state != replay.final_state:
        raise ValueError("COUNTERFACTUAL_REPLAY_STATE_MISMATCH")

    valuation = value_portfolio(
        ledger,
        {instrument_id: mark_bar.close},
        horizon,
    )
    return CounterfactualHorizonValuation(mark_bar=mark_bar, valuation=valuation)


def _counterfactual_signal_id(
    signal: Signal,
    policy: CounterfactualAcceptancePolicy,
) -> UUID:
    quantity = format(policy.quantity.normalize(), "f")
    holding = policy.holding_period
    policy_key = "|".join(
        (
            str(signal.signal_id),
            policy.policy_id,
            policy.version,
            quantity,
            str(holding.days),
            str(holding.seconds),
            str(holding.microseconds),
            policy.execution_assumption,
            policy.exit_assumption.value,
        )
    )
    return uuid5(NAMESPACE_URL, f"hope:counterfactual:{policy_key}:signal")
