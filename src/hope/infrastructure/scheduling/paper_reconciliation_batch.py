"""Bounded, atomic recovery of an explicitly selected set of PAPER runs."""

from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from typing import Iterable
from uuid import UUID

from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from hope.application.jobs import ScheduledJobRun
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_control import (
    SqlAlchemyPaperEnvironmentControlRepository,
)
from hope.infrastructure.scheduling.paper_reconciliation import (
    _job_identity_checked,
    _reconcile_completed_paper_run,
    _validated_reconciliation_current,
)


MAX_PAPER_RECONCILIATION_BATCH = 32


@dataclass(frozen=True)
class PaperReconciliationBatchResult:
    reconciled_run_ids: tuple[UUID, ...]
    already_reconciled_run_ids: tuple[UUID, ...]


def reconcile_completed_paper_batch(
    connection: Connection,
    job_runs: Iterable[ScheduledJobRun],
    *,
    current: datetime,
    max_runs: int = MAX_PAPER_RECONCILIATION_BATCH,
) -> PaperReconciliationBatchResult:
    """Verify and terminalize at most 32 runs, or roll back all batch changes.

    Selection is explicit: this never discovers, claims, or executes trading work.
    Input consumption is bounded even for a generator. All identities and clocks
    are checked before touching the database. Lock order is independent of input
    order, and the existing environment control lock precedes all job locks.
    Each run must pass the same lineage, accounting and receipt proof as single
    run reconciliation, including already-reconciled runs. A missing or partial
    run aborts the whole batch. The caller owns the outer transaction and commit;
    a returned result is provisional until that commit succeeds.
    """
    if type(max_runs) is not int or not 1 <= max_runs <= MAX_PAPER_RECONCILIATION_BATCH:
        raise ValueError("PAPER_RECONCILIATION_BATCH_LIMIT_INVALID")
    selected = tuple(islice(job_runs, max_runs + 1))
    if len(selected) > max_runs:
        raise ValueError("PAPER_RECONCILIATION_BATCH_LIMIT_EXCEEDED")
    if not selected:
        raise ValueError("PAPER_RECONCILIATION_BATCH_EMPTY")
    for run in selected:
        current = _validated_reconciliation_current(run, current)
    if len({run.job_run_id for run in selected}) != len(selected):
        raise ValueError("PAPER_RECONCILIATION_BATCH_DUPLICATE_RUN")
    ordered = sorted(selected, key=lambda run: run.job_run_id.int)
    reconciled, existing = [], []
    try:
        with connection.begin_nested():
            SqlAlchemyPaperEnvironmentControlRepository(connection).assert_running()
            repository = SqlAlchemyJobRunRepository(connection)
            claimed, terminal = [], []
            for run in ordered:
                # False may mean a terminal run. Its receipt is authenticated
                # below while the row lock remains held; it is never skipped.
                is_claimed = _job_identity_checked(
                    lambda run=run: repository.lock_claimed_for_reconciliation(run)
                )
                (claimed if is_claimed else terminal).append(run)
            # Prior terminal receipts reverify the complete current portfolio
            # history. Reconcile selected in-flight owners first so an older
            # receipt cannot reject a valid newer accounting owner in this same
            # batch. Lock acquisition remains canonical; all proofs and any
            # transitions still share the batch savepoint and owner checks.
            for run in (*claimed, *terminal):
                changed = _reconcile_completed_paper_run(connection, run, current=current)
                (reconciled if changed else existing).append(run.job_run_id)
    except SQLAlchemyError as exc:
        raise RuntimeError("PAPER_RECONCILIATION_BATCH_DATABASE_UNAVAILABLE") from exc
    return PaperReconciliationBatchResult(tuple(reconciled), tuple(existing))
