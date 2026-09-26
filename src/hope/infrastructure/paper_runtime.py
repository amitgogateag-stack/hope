from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Callable, Iterable, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import Connection, Engine

from hope.application.jobs import JobRunStatus, ScheduledJobRun
from hope.application.paper.fill_accounting import PaperFillAccountingWriter
from hope.application.paper.jobs import PaperEntryOrderDecisionJob, PaperStrategyDecisionJob
from hope.application.paper.orders import PaperOrderWriter
from hope.application.paper.risk import PaperRiskWriter
from hope.application.paper.runner import PaperCycleOutcome, PaperCycleRunner, PaperRuntimeContext
from hope.application.paper.signals import PaperSignalWriter
from hope.domain.execution.models import OrderSide
from hope.domain.risk.inputs import PortfolioEntryRiskInputs
from hope.domain.risk.portfolio import PortfolioRiskEngine
from hope.domain.signal.models import Signal, SignalType
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.market_contexts import PITMarketContextRepository
from hope.infrastructure.repositories.market_intelligence_assessments import (
    SqlAlchemyIntelligenceAssessmentRepository,
)
from hope.infrastructure.repositories.paper_fill_accounting import SqlAlchemyPaperFillAccountingRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_risk import SqlAlchemyPaperRiskRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
from hope.infrastructure.repositories.universe_snapshots import UniverseSnapshotRepository


PaperJobWork = Callable[[PaperRuntimeContext], None]


@runtime_checkable
class ConnectionBoundPaperJob(Protocol):
    """Registered PAPER work that must be bound to the caller-owned DB transaction."""

    def bind(self, connection: Connection) -> PaperJobWork:
        ...


@dataclass(frozen=True)
class PaperMarketDataFreshnessPolicy:
    """Explicit maximum market-data age permitted for autonomous PAPER decisions."""

    max_age: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.max_age, timedelta):
            raise TypeError("PAPER_MARKET_DATA_FRESHNESS_REQUIRES_TIMEDELTA")
        if self.max_age <= timedelta(0):
            raise ValueError("PAPER_MARKET_DATA_FRESHNESS_MUST_BE_POSITIVE")

    def assert_context_fresh(
        self,
        market_context,
        active_instrument_ids: tuple[UUID, ...],
    ) -> None:
        for instrument_id in active_instrument_ids:
            bars = market_context.for_instrument(str(instrument_id))
            if not bars:
                raise RuntimeError("PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT")
            latest_event_time = max(bar.event_time for bar in bars)
            if market_context.as_of - latest_event_time > self.max_age:
                raise RuntimeError("PAPER_MARKET_DATA_STALE")


@dataclass(frozen=True)
class DurableUniversePaperStrategyDecision:
    """Bind one strategy decision to persisted universe and market-data inputs."""

    strategy: Strategy
    dataset_version_id: UUID
    as_of: datetime
    universe_version_id: UUID
    parameters: ParameterSnapshot
    freshness_policy: PaperMarketDataFreshnessPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, Strategy):
            raise TypeError("PAPER_DURABLE_DECISION_REQUIRES_STRATEGY")
        if not isinstance(self.dataset_version_id, UUID):
            raise TypeError("PAPER_DURABLE_DECISION_REQUIRES_DATASET_VERSION_ID")
        if not isinstance(self.as_of, datetime):
            raise TypeError("PAPER_DURABLE_DECISION_REQUIRES_AS_OF")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("PAPER_DURABLE_DECISION_REQUIRES_AWARE_AS_OF")
        if not isinstance(self.universe_version_id, UUID):
            raise TypeError("PAPER_DURABLE_DECISION_REQUIRES_UNIVERSE_VERSION_ID")
        if not isinstance(self.parameters, ParameterSnapshot):
            raise TypeError("PAPER_DURABLE_DECISION_REQUIRES_PARAMETER_SNAPSHOT")
        if not isinstance(self.freshness_policy, PaperMarketDataFreshnessPolicy):
            raise TypeError("PAPER_DURABLE_DECISION_REQUIRES_FRESHNESS_POLICY")

    def bind(self, connection: Connection) -> PaperJobWork:
        snapshot = UniverseSnapshotRepository(connection).get(self.universe_version_id)
        if snapshot is None:
            raise RuntimeError("PAPER_DURABLE_DECISION_UNIVERSE_NOT_FOUND")
        market_context = PITMarketContextRepository(connection).get(
            self.dataset_version_id,
            as_of=self.as_of,
            universe_version_id=self.universe_version_id,
            instrument_ids=tuple(member.instrument_id for member in snapshot.members),
        )
        self.freshness_policy.assert_context_fresh(
            market_context,
            snapshot.active_instrument_ids(self.as_of),
        )
        return PaperStrategyDecisionJob(
            self.strategy,
            market_context,
            snapshot,
            self.parameters,
            dataset_version_id=self.dataset_version_id,
        )


PaperRegisteredWork = PaperJobWork | ConnectionBoundPaperJob


@dataclass(frozen=True)
class DurablePaperEntryOrderDecision:
    """Resolve the intelligence entry gate from durable history inside the PAPER transaction."""

    signal: Signal
    risk_inputs: PortfolioEntryRiskInputs
    risk_engine: PortfolioRiskEngine
    side: OrderSide
    policy_version: str = "hope.intelligence-policy.v1"

    def __post_init__(self) -> None:
        if not isinstance(self.signal, Signal):
            raise TypeError("PAPER_DURABLE_ENTRY_REQUIRES_SIGNAL")
        if self.signal.signal_type is not SignalType.ENTRY:
            raise ValueError("PAPER_DURABLE_ENTRY_REQUIRES_ENTRY_SIGNAL")
        if not isinstance(self.risk_inputs, PortfolioEntryRiskInputs):
            raise TypeError("PAPER_DURABLE_ENTRY_REQUIRES_RISK_INPUTS")
        request = self.risk_inputs.request
        if request.signal_id != self.signal.signal_id:
            raise ValueError("PAPER_DURABLE_ENTRY_RISK_SIGNAL_MISMATCH")
        if request.instrument_id != self.signal.instrument_id:
            raise ValueError("PAPER_DURABLE_ENTRY_RISK_INSTRUMENT_MISMATCH")
        if request.strategy_version != self.signal.strategy_version:
            raise ValueError("PAPER_DURABLE_ENTRY_RISK_STRATEGY_VERSION_MISMATCH")
        if not isinstance(self.risk_engine, PortfolioRiskEngine):
            raise TypeError("PAPER_DURABLE_ENTRY_REQUIRES_RISK_ENGINE")
        if not isinstance(self.side, OrderSide):
            raise TypeError("PAPER_DURABLE_ENTRY_REQUIRES_ORDER_SIDE")
        if not self.policy_version or self.policy_version != self.policy_version.strip():
            raise ValueError("PAPER_DURABLE_ENTRY_POLICY_NOT_CANONICAL")
        if self.policy_version != "hope.intelligence-policy.v1":
            raise ValueError("PAPER_DURABLE_ENTRY_POLICY_UNSUPPORTED")

    def bind(self, connection: Connection) -> PaperJobWork:
        intelligence_gate = SqlAlchemyIntelligenceAssessmentRepository(
            connection
        ).entry_gate_context(
            self.signal.instrument_id,
            as_of=self.signal.decision_time,
            policy_version=self.policy_version,
        )
        return PaperEntryOrderDecisionJob(
            self.signal,
            self.risk_inputs,
            self.risk_engine,
            self.side,
            intelligence_gate,
        )


@dataclass(frozen=True)
class PaperJobDefinition:
    """One explicitly approved PAPER application job and its durable job key."""

    job_key: str
    work: PaperRegisteredWork

    def __post_init__(self) -> None:
        key = self.job_key.strip()
        if not key:
            raise ValueError("PAPER_JOB_KEY_REQUIRED")
        if key != self.job_key:
            raise ValueError("PAPER_JOB_KEY_NOT_CANONICAL")
        if not callable(self.work) and not isinstance(self.work, ConnectionBoundPaperJob):
            raise TypeError("PAPER_JOB_HANDLER_MUST_BE_CALLABLE")


class PaperJobRegistry:
    """Immutable allow-list of explicitly defined scheduled PAPER jobs."""

    def __init__(self, definitions: Iterable[PaperJobDefinition]) -> None:
        handlers: dict[str, PaperRegisteredWork] = {}
        for definition in definitions:
            if not isinstance(definition, PaperJobDefinition):
                raise TypeError("PAPER_JOB_DEFINITION_REQUIRED")
            if definition.job_key in handlers:
                raise ValueError("PAPER_JOB_KEY_DUPLICATE")
            handlers[definition.job_key] = definition.work
        self._handlers = MappingProxyType(handlers)

    def resolve(self, job_run: ScheduledJobRun) -> PaperRegisteredWork:
        """Resolve only an explicitly registered handler for this durable job identity."""
        handler = self._handlers.get(job_run.job_key)
        if handler is None:
            raise RuntimeError("PAPER_JOB_NOT_REGISTERED")
        return handler


class SqlAlchemyPaperRuntime:
    """Concrete PAPER composition root bound to one caller-owned DB connection."""

    def __init__(
        self,
        connection: Connection,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._runner = PaperCycleRunner(
            SqlAlchemyJobRunRepository(connection),
            now=now,
        )
        self._signal_writer = PaperSignalWriter(SqlAlchemyPaperSignalRepository(connection))
        self._risk_writer = PaperRiskWriter(SqlAlchemyPaperRiskRepository(connection))
        self._order_writer = PaperOrderWriter(SqlAlchemyPaperOrderRepository(connection))
        self._fill_writer = PaperFillAccountingWriter(
            SqlAlchemyPaperFillAccountingRepository(connection)
        )

    def _run_registered(
        self,
        job_run: ScheduledJobRun,
        work: PaperJobWork,
    ) -> PaperCycleOutcome:
        """Internal execution primitive; one-shot callers must resolve work via the registry."""
        return self._runner.run_runtime(
            job_run,
            self._signal_writer,
            self._risk_writer,
            self._order_writer,
            self._fill_writer,
            work,
        )

    def quarantine_incomplete_claim(self, job_run: ScheduledJobRun) -> PaperCycleOutcome:
        """Expose only the explicit no-replay quarantine lifecycle action."""
        return self._runner.quarantine_incomplete_claim(job_run)


def run_paper_once(
    engine: Engine,
    job_run: ScheduledJobRun,
    registry: PaperJobRegistry,
    *,
    now: Callable[[], datetime],
) -> PaperCycleOutcome:
    """Execute exactly one registered PAPER job and durably preserve terminal state.

    Unknown jobs fail before any lifecycle claim. Connection-bound work is resolved only after
    the caller-owned transaction starts, so durable inputs are read from the same transaction
    that claims and executes the scheduled job. Application work errors are re-raised only after
    the transaction commits the runner's FAILED terminal record. Database/commit failures remain
    fail-closed and are not converted into application success.
    """
    if not isinstance(registry, PaperJobRegistry):
        raise TypeError("PAPER_ONE_SHOT_REQUIRES_JOB_REGISTRY")
    registered_work = registry.resolve(job_run)

    outcome: PaperCycleOutcome | None = None
    work_error: Exception | None = None

    with engine.begin() as connection:
        runtime = SqlAlchemyPaperRuntime(connection, now=now)
        try:
            work = (
                registered_work.bind(connection)
                if isinstance(registered_work, ConnectionBoundPaperJob)
                else registered_work
            )
            outcome = runtime._run_registered(job_run, work)
        except Exception as exc:
            record = SqlAlchemyJobRunRepository(connection).get_record(job_run.job_run_id)
            if record is None or record.status is not JobRunStatus.FAILED:
                raise
            work_error = exc

    if work_error is not None:
        raise work_error
    if outcome is None:
        raise RuntimeError("PAPER_ONE_SHOT_OUTCOME_MISSING")
    return outcome
