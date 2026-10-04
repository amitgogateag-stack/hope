from __future__ import annotations

from datetime import datetime
from typing import Callable, TypeVar

from hope.application.jobs import (
    JobRunStatus,
    ScheduledJobRun,
    create_job_run_completion,
)
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_reconciliation_audit import (
    SqlAlchemyPaperReconciliationAuditRepository,
)
from hope.infrastructure.scheduling.recovery import (
    PaperRecoveryDecision,
    assess_due_paper_recovery,
)
from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


_T = TypeVar("_T")


def _job_identity_checked(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except ValueError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_IDENTITY_MISMATCH") from exc


def reconcile_completed_paper_run(
    connection: Connection,
    job_run: ScheduledJobRun,
    *,
    current: datetime,
) -> bool:
    """Terminalize one proven-complete interrupted PAPER run without replaying effects.

    The durable job row is locked before recovery evidence is assessed. PAPER effect
    insertion takes the same row lock at the database boundary, so no new effect can
    appear between proof and terminalization. This operation never invokes signal,
    risk, order, fill, position, accounting, or P&L writers. A concurrent reconciler
    that already terminalized the run as SUCCEEDED is accepted only when its exact
    immutable reconciliation receipt is durable. The full proof, transition,
    durable reread, effect comparison, and receipt write execute inside a savepoint
    so any fail-closed error rolls back the transition even if a caller catches it.
    """
    if not job_run.job_key.startswith("paper:"):
        raise RuntimeError("PAPER_JOB_RECONCILIATION_REQUIRES_PAPER_JOB")
    try:
        with connection.begin_nested():
            return _reconcile_completed_paper_run(
                connection,
                job_run,
                current=current,
            )
    except SQLAlchemyError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_DATABASE_UNAVAILABLE") from exc


def _reconcile_completed_paper_run(
    connection: Connection,
    job_run: ScheduledJobRun,
    *,
    current: datetime,
) -> bool:
    repository = SqlAlchemyJobRunRepository(connection)
    effects_repository = SqlAlchemyPaperEffectRepository(connection)
    audit_repository = SqlAlchemyPaperReconciliationAuditRepository(connection)
    locked_claimed = _job_identity_checked(
        lambda: repository.lock_claimed_for_reconciliation(job_run)
    )
    if not locked_claimed:
        durable = _job_identity_checked(
            lambda: repository.get_record_for_run(job_run)
        )
        if durable is not None and durable.status is JobRunStatus.SUCCEEDED and durable.failure_code is None:
            effects = effects_repository.list_for_job_run(job_run.job_run_id)
            try:
                verified = audit_repository.verify(durable, effects)
            except ValueError as exc:
                raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_MISMATCH") from exc
            if not verified:
                raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_NOT_DURABLE")
            return False
        raise RuntimeError("PAPER_JOB_RECONCILIATION_NOT_CLAIMED")

    effects_before = effects_repository.list_for_job_run(job_run.job_run_id)
    report = assess_due_paper_recovery(
        connection,
        (job_run,),
        current=current,
        max_lateness=current - job_run.scheduled_for,
    )
    if len(report.assessments) != 1 or report.assessments[0].job_run_id != job_run.job_run_id:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_ASSESSMENT_MISMATCH")
    assessment = report.assessments[0]
    if assessment.decision is not PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_NOT_PROVEN")

    completion = create_job_run_completion(
        job_run,
        JobRunStatus.SUCCEEDED,
        current,
    )
    transitioned = _job_identity_checked(
        lambda: repository.complete(completion)
    )
    if not transitioned:
        # The CLAIMED row is still held FOR UPDATE, so another lifecycle writer
        # cannot legitimately win between the proof and this transition.
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TRANSITION_NOT_APPLIED")

    durable = _job_identity_checked(
        lambda: repository.get_record_for_run(job_run)
    )
    if durable is None or durable.status is not JobRunStatus.SUCCEEDED:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TERMINAL_STATE_NOT_DURABLE")
    if durable.failure_code is not None:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TERMINAL_STATE_MISMATCH")
    if durable.completed_at != completion.completed_at:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TERMINAL_STATE_MISMATCH")
    effects_after = effects_repository.list_for_job_run(job_run.job_run_id)
    if effects_after != effects_before:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_EFFECTS_CHANGED")
    try:
        audit_recorded = audit_repository.record(durable, effects_after)
    except IntegrityError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_REJECTED") from exc
    except ValueError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_MISMATCH") from exc
    if not audit_recorded:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_NOT_RECORDED")
    try:
        audit_verified = audit_repository.verify(durable, effects_after)
    except ValueError as exc:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_MISMATCH") from exc
    if not audit_verified:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_AUDIT_NOT_DURABLE")
    return True
