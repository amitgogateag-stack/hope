from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Callable, Iterable, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import Connection, Engine

from hope.application.jobs import ScheduledJobRun
from hope.application.paper.fill_accounting import PaperFillAccountingWriter
from hope.application.paper.jobs import PaperStrategyDecisionJob
from hope.application.paper.orders import PaperOrderWriter
from hope.application.paper.runner import PaperCycleOutcome, PaperCycleRunner, PaperRuntimeContext
from hope.application.paper.signals import PaperSignalWriter
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.market_contexts import PITMarketContextRepository
from hope.infrastructure.repositories.paper_fill_accounting import SqlAlchemyPaperFillAccountingRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository
from hope.infrastructure.repositories.universe_snapshots import UniverseSnapshotRepository


PaperJobWork = Callable[[PaperRuntimeContext], None]


@runtime_checkable
class ConnectionBoundPaperJob(Protocol):
    """Registered PAPER work that must be bound to the caller-owned DB transaction."""

    def bind(self, connection: Connection) -> PaperJobWork:
        ...


@dataclass(frozen=True)
class DurableUniversePaperStrategyDecision:
    """Bind one strategy decision to persisted universe and market-data inputs."""

    strategy: Strategy
    dataset_version_id: UUID
    as_of: datetime
    universe_version_id: UUID
    parameters: ParameterSnapshot

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

    def bind(self, connection: Connection) -> PaperJobWork:
        snapshot = UniverseSnapshotRepository(connection).get(self.universe_version_id)
        if snapshot is None:
            raise RuntimeError("PAPER_DURABLE_DECISION_UNIVERSE_NOT_FOUND")
        market_context = PITMarketContextRepository(connection).get(
            self.dataset_version_id,
            as_of=self.as_of,
            instrument_ids=tuple(member.instrument_id for member in snapshot.members),
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
            work_error = exc

    if work_error is not None:
        raise work_error
    if outcome is None:
        raise RuntimeError("PAPER_ONE_SHOT_OUTCOME_MISSING")
    return outcome
