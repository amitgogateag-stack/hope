from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal
from uuid import UUID

from hope.application.paper.provenance import paper_decision_inputs_hash
from hope.application.paper.runner import PaperRuntimeContext
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.execution.models import Environment, Order
from hope.domain.execution.simulator import Fill
from hope.domain.market_data.context import PITMarketContext
from hope.domain.signal.models import Signal
from hope.domain.strategy.models import ParameterSnapshot, Strategy


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

        universe_member_ids = {member.instrument_id for member in self.universe.members}
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
            if signal.instrument_id not in universe_member_ids:
                raise ValueError("PAPER_STRATEGY_SIGNAL_OUTSIDE_UNIVERSE")

        for signal in signals:
            runtime.record_signal(signal)


@dataclass(frozen=True)
class PaperOrderPersistenceJob:
    """Persist one already-decided PAPER order through the authoritative runtime boundary."""

    order: Order

    def __post_init__(self) -> None:
        if not isinstance(self.order, Order):
            raise TypeError("PAPER_ORDER_JOB_REQUIRES_ORDER")
        if self.order.environment is not Environment.PAPER:
            raise ValueError("PAPER_ORDER_JOB_REQUIRES_PAPER_ENVIRONMENT")

    def __call__(self, runtime: PaperRuntimeContext) -> None:
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
