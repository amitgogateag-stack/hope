from __future__ import annotations

from datetime import datetime

from hope.application.jobs import (
    JobRunStatus,
    ScheduledJobRun,
    create_job_run_completion,
)
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.scheduling.recovery import (
    PaperRecoveryDecision,
    assess_due_paper_recovery,
)
from sqlalchemy import Connection


def reconcile_completed_paper_run(
    connection: Connection,
    job_run: ScheduledJobRun,
    *,
    current: datetime,
) -> bool:
    """Terminalize one proven-complete interrupted PAPER run without replaying effects.

    This operation only updates the durable job lifecycle. It never invokes signal,
    risk, order, fill, position, accounting, or P&L writers.
    """
    report = assess_due_paper_recovery(
        connection,
        (job_run,),
        current=current,
        max_lateness=current - job_run.scheduled_for,
    )
    assessment = report.assessments[0]
    if assessment.decision is not PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_NOT_PROVEN")

    repository = SqlAlchemyJobRunRepository(connection)
    completion = create_job_run_completion(
        job_run,
        JobRunStatus.SUCCEEDED,
        current,
    )
    transitioned = repository.complete(completion)

    durable = repository.get_record_for_run(job_run)
    if durable is None or durable.status is not JobRunStatus.SUCCEEDED:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TERMINAL_STATE_NOT_DURABLE")
    if durable.completed_at != completion.completed_at or durable.failure_code is not None:
        raise RuntimeError("PAPER_JOB_RECONCILIATION_TERMINAL_STATE_MISMATCH")
    return transitioned
