from __future__ import annotations

from datetime import datetime
from types import MappingProxyType
from typing import Callable, Mapping

from sqlalchemy import Connection, Engine

from hope.application.jobs import ScheduledJobRun
from hope.application.paper.fill_accounting import PaperFillAccountingWriter
from hope.application.paper.orders import PaperOrderWriter
from hope.application.paper.runner import PaperCycleOutcome, PaperCycleRunner, PaperRuntimeContext
from hope.application.paper.signals import PaperSignalWriter
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_fill_accounting import SqlAlchemyPaperFillAccountingRepository
from hope.infrastructure.repositories.paper_orders import SqlAlchemyPaperOrderRepository
from hope.infrastructure.repositories.paper_signals import SqlAlchemyPaperSignalRepository


PaperJobWork = Callable[[PaperRuntimeContext], None]


class PaperJobRegistry:
    """Immutable allow-list mapping scheduled PAPER job keys to approved work handlers."""

    def __init__(self, handlers: Mapping[str, PaperJobWork]) -> None:
        normalized: dict[str, PaperJobWork] = {}
        for job_key, handler in handlers.items():
            key = job_key.strip()
            if not key:
                raise ValueError("PAPER_JOB_KEY_REQUIRED")
            if key != job_key:
                raise ValueError("PAPER_JOB_KEY_NOT_CANONICAL")
            if not callable(handler):
                raise TypeError("PAPER_JOB_HANDLER_MUST_BE_CALLABLE")
            normalized[key] = handler
        self._handlers = MappingProxyType(normalized)

    def resolve(self, job_run: ScheduledJobRun) -> PaperJobWork:
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

    Unknown jobs fail before any lifecycle claim. Application work errors are re-raised only
    after the transaction commits the runner's FAILED terminal record. Database/commit failures
    remain fail-closed and are not converted into application success.
    """
    if not isinstance(registry, PaperJobRegistry):
        raise TypeError("PAPER_ONE_SHOT_REQUIRES_JOB_REGISTRY")
    work = registry.resolve(job_run)

    outcome: PaperCycleOutcome | None = None
    work_error: Exception | None = None

    with engine.begin() as connection:
        runtime = SqlAlchemyPaperRuntime(connection, now=now)
        try:
            outcome = runtime._run_registered(job_run, work)
        except Exception as exc:
            work_error = exc

    if work_error is not None:
        raise work_error
    if outcome is None:
        raise RuntimeError("PAPER_ONE_SHOT_OUTCOME_MISSING")
    return outcome
