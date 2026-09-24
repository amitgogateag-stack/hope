from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal
from uuid import UUID

from hope.application.paper.provenance import paper_decision_inputs_hash
from hope.application.paper.runner import PaperRuntimeContext
from hope.application.trading.risk import assess_portfolio_entry_signal
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.execution.models import Environment, Order, OrderSide
from hope.domain.execution.simulator import Fill
from hope.domain.market_data.context import PITMarketContext
from hope.domain.market_intelligence.gate import IntelligenceEntryGateContext
from hope.domain.risk.inputs import PortfolioEntryRiskInputs
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.risk.portfolio import PortfolioRiskEngine
from hope.domain.signal.models import Signal
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.domain.trading.kernel import create_order_intent, materialize_order


@dataclass(frozen=True)
class PaperSignalPersistenceJob:
    """Persist one already-decided PAPER signal through the authoritative runtime boundary."""

    signal: Signal

    def __post_init__(self) -> None:
        if not isinstance(self.signal, Signal):
            raise TypeError("PAPER_SIGNAL_JOB_REQUIRES_SIGNAL")

    def __call__(self, runtime: PaperRuntimeContext) -> None:
        decision_time = self.signal.decision_time
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("PAPER_SIGNAL_DECISION_TIME_MUST_BE_TIMEZONE_AWARE")
        if decision_time.astimezone(timezone.utc) > runtime.cycle.job_run.scheduled_for:
            raise ValueError("PAPER_SIGNAL_DECISION_AFTER_JOB_SCHEDULE")
        runtime.record_signal(self.signal)


@dataclass(frozen=True)
class PaperStrategyDecisionJob:
    """Generate and persist PAPER signals from one explicit PIT-safe strategy decision."""

    strategy: Strategy
    market_context: PITMarketContext
    universe: UniverseSnapshot
    parameters: ParameterSnapshot
    dataset_version_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, Strategy):
            raise TypeError("PAPER_DECISION_JOB_REQUIRES_STRATEGY")
        if not isinstance(self.market_context, PITMarketContext):
            raise TypeError("PAPER_DECISION_JOB_REQUIRES_PIT_MARKET_CONTEXT")
        if not isinstance(self.universe, UniverseSnapshot):
            raise TypeError("PAPER_DECISION_JOB_REQUIRES_UNIVERSE_SNAPSHOT")
        if not self.universe.version.pit_certified:
            raise ValueError("PAPER_DECISION_JOB_REQUIRES_PIT_CERTIFIED_UNIVERSE")
        if not isinstance(self.parameters, ParameterSnapshot):
            raise TypeError("PAPER_DECISION_JOB_REQUIRES_PARAMETER_SNAPSHOT")
        if self.dataset_version_id is not None and not isinstance(self.dataset_version_id, UUID):
            raise TypeError("PAPER_DECISION_JOB_REQUIRES_DATASET_VERSION_ID")

    def __call__(self, runtime: PaperRuntimeContext) -> None:
        as_of = self.market_context.as_of
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("PAPER_DECISION_TIME_MUST_BE_TIMEZONE_AWARE")
        if as_of.astimezone(timezone.utc) > runtime.cycle.job_run.scheduled_for:
            raise ValueError("PAPER_DECISION_AFTER_JOB_SCHEDULE")

        expected_inputs_hash = paper_decision_inputs_hash(
            self.strategy,
            self.market_context,
            self.universe,
            self.parameters,
            dataset_version_id=self.dataset_version_id,
        )
        signals = self.strategy.generate_signals(
            self.market_context,
            self.universe.version,
            self.universe.members,
            self.parameters,
            expected_inputs_hash,
        )
        if not isinstance(signals, tuple):
            raise TypeError("PAPER_STRATEGY_SIGNALS_MUST_BE_TUPLE")

        universe_members_by_id = {
            member.instrument_id: member for member in self.universe.members
        }
        for signal in signals:
            if not isinstance(signal, Signal):
                raise TypeError("PAPER_STRATEGY_OUTPUT_REQUIRES_SIGNAL")
            decision_time = signal.decision_time
            if decision_time.tzinfo is None or decision_time.utcoffset() is None:
                raise ValueError("PAPER_STRATEGY_SIGNAL_TIME_MUST_BE_TIMEZONE_AWARE")
            if decision_time != as_of:
                raise ValueError("PAPER_STRATEGY_SIGNAL_DECISION_TIME_MISMATCH")
            if signal.strategy_version != self.strategy.version:
                raise ValueError("PAPER_STRATEGY_SIGNAL_VERSION_MISMATCH")
            if signal.inputs_hash != expected_inputs_hash:
                raise ValueError("PAPER_STRATEGY_SIGNAL_INPUTS_HASH_MISMATCH")
            member = universe_members_by_id.get(signal.instrument_id)
            if member is None:
                raise ValueError("PAPER_STRATEGY_SIGNAL_OUTSIDE_UNIVERSE")
            if (
                member.valid_from is not None
                and decision_time < member.valid_from
            ) or (
                member.valid_to is not None
                and decision_time >= member.valid_to
            ):
                raise ValueError(
                    "PAPER_STRATEGY_SIGNAL_OUTSIDE_ACTIVE_UNIVERSE_MEMBERSHIP"
                )

        for signal in signals:
            runtime.record_signal(signal)


@dataclass(frozen=True)
class PaperEntryOrderDecisionJob:
    """Risk-assess one persisted ENTRY signal before materializing any PAPER order."""

    signal: Signal
    risk_inputs: PortfolioEntryRiskInputs
    risk_engine: PortfolioRiskEngine
    side: OrderSide
    intelligence_gate: IntelligenceEntryGateContext

    def __post_init__(self) -> None:
        if not isinstance(self.signal, Signal):
            raise TypeError("PAPER_ENTRY_ORDER_JOB_REQUIRES_SIGNAL")
        if not isinstance(self.risk_inputs, PortfolioEntryRiskInputs):
            raise TypeError("PAPER_ENTRY_ORDER_JOB_REQUIRES_RISK_INPUTS")
        if not isinstance(self.risk_engine, PortfolioRiskEngine):
            raise TypeError("PAPER_ENTRY_ORDER_JOB_REQUIRES_RISK_ENGINE")
        if not isinstance(self.side, OrderSide):
            raise TypeError("PAPER_ENTRY_ORDER_JOB_REQUIRES_ORDER_SIDE")
        if not isinstance(
            self.intelligence_gate,
            IntelligenceEntryGateContext,
        ):
            raise TypeError("PAPER_ENTRY_ORDER_JOB_REQUIRES_INTELLIGENCE_GATE_CONTEXT")

    def __call__(self, runtime: PaperRuntimeContext) -> None:
        decision_time = self.signal.decision_time
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise ValueError("PAPER_ENTRY_RISK_SIGNAL_TIME_MUST_BE_TIMEZONE_AWARE")
        if decision_time.astimezone(timezone.utc) > runtime.cycle.job_run.scheduled_for:
            raise ValueError("PAPER_ENTRY_RISK_SIGNAL_AFTER_JOB_SCHEDULE")

        assessment = assess_portfolio_entry_signal(
            self.signal,
            self.risk_inputs,
            self.risk_engine,
            intelligence_gate=self.intelligence_gate,
        )
        runtime.record_risk(assessment)

        intent = create_order_intent(
            self.signal,
            assessment,
            self.side,
            Environment.PAPER,
        )
        if intent is None:
            return

        order = materialize_order(
            intent,
            runtime.cycle.order_id(self.signal.signal_id),
        )
        runtime.record_order(order)


@dataclass(frozen=True)
class PaperOrderPersistenceJob:
    """Persist one risk-approved PAPER order through the authoritative runtime boundary."""

    order: Order
    assessment: RiskAssessment

    def __post_init__(self) -> None:
        if not isinstance(self.order, Order):
            raise TypeError("PAPER_ORDER_JOB_REQUIRES_ORDER")
        if self.order.environment is not Environment.PAPER:
            raise ValueError("PAPER_ORDER_JOB_REQUIRES_PAPER_ENVIRONMENT")
        if not isinstance(self.assessment, RiskAssessment):
            raise TypeError("PAPER_ORDER_JOB_REQUIRES_RISK_ASSESSMENT")
        if self.assessment.signal_id != self.order.signal_id:
            raise ValueError("PAPER_ORDER_JOB_RISK_SIGNAL_MISMATCH")
        if self.assessment.decision is not RiskDecision.APPROVE:
            raise ValueError("PAPER_ORDER_JOB_REQUIRES_RISK_APPROVAL")
        if self.assessment.approved_quantity != self.order.quantity:
            raise ValueError("PAPER_ORDER_JOB_RISK_QUANTITY_MISMATCH")

    def __call__(self, runtime: PaperRuntimeContext) -> None:
        runtime.record_risk(self.assessment)
        runtime.record_order(self.order)


@dataclass(frozen=True)
class PaperFillAccountingJob:
    """Persist one PAPER fill and its authoritative portfolio-accounting effects."""

    portfolio_id: UUID
    initial_cash: Decimal
    fill: Fill
    sequence: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.portfolio_id, UUID):
            raise TypeError("PAPER_FILL_JOB_REQUIRES_PORTFOLIO_ID")
        if not isinstance(self.initial_cash, Decimal):
            raise TypeError("PAPER_FILL_JOB_REQUIRES_DECIMAL_INITIAL_CASH")
        if not self.initial_cash.is_finite() or self.initial_cash < 0:
            raise ValueError("PAPER_FILL_JOB_INITIAL_CASH_INVALID")
        if not isinstance(self.fill, Fill):
            raise TypeError("PAPER_FILL_JOB_REQUIRES_FILL")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("PAPER_FILL_JOB_SEQUENCE_INVALID")

    def __call__(self, runtime: PaperRuntimeContext) -> None:
        fill_time = self.fill.fill_time
        if fill_time is None or fill_time.tzinfo is None or fill_time.utcoffset() is None:
            raise ValueError("PAPER_FILL_TIME_MUST_BE_TIMEZONE_AWARE")
        if fill_time.astimezone(timezone.utc) > runtime.cycle.job_run.scheduled_for:
            raise ValueError("PAPER_FILL_AFTER_JOB_SCHEDULE")
        runtime.record_fill(
            self.portfolio_id,
            self.initial_cash,
            self.fill,
            sequence=self.sequence,
        )
