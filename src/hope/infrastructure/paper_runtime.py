from __future__ import annotations

from datetime import datetime
from typing import Callable

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

    def run(
        self,
        job_run: ScheduledJobRun,
        work: Callable[[PaperRuntimeContext], None],
    ) -> PaperCycleOutcome:
        """Run one PAPER job through the authoritative durable writer set."""
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
    work: Callable[[PaperRuntimeContext], None],
    *,
    now: Callable[[], datetime],
) -> PaperCycleOutcome:
    """Execute exactly one PAPER job and durably preserve its terminal lifecycle state.

    Application work errors are re-raised only after the transaction commits the runner's
    FAILED terminal record. Database/commit failures remain fail-closed and are not converted
    into application success.
    """
    outcome: PaperCycleOutcome | None = None
    work_error: Exception | None = None

    with engine.begin() as connection:
        runtime = SqlAlchemyPaperRuntime(connection, now=now)
        try:
            outcome = runtime.run(job_run, work)
        except Exception as exc:
            work_error = exc

    if work_error is not None:
        raise work_error
    if outcome is None:
        raise RuntimeError("PAPER_ONE_SHOT_OUTCOME_MISSING")
    return outcome
