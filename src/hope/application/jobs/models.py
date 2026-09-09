from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import NAMESPACE_URL, UUID, uuid5


class JobRunStatus(str, Enum):
    CLAIMED = "CLAIMED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ScheduledJobRun:
    """Durable identity for one scheduled invocation of a named HOPE job."""

    job_run_id: UUID
    job_key: str
    scheduled_for: datetime


@dataclass(frozen=True)
class JobRunRecord:
    """Durable lifecycle state for one scheduled job run."""

    run: ScheduledJobRun
    status: JobRunStatus
    completed_at: datetime | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        try:
            status = JobRunStatus(self.status)
        except ValueError as exc:
            raise ValueError("JOB_RUN_STATUS_INVALID") from exc
        object.__setattr__(self, "status", status)

        if status is JobRunStatus.CLAIMED:
            if self.completed_at is not None or self.failure_code is not None:
                raise ValueError("JOB_CLAIMED_STATE_MUST_BE_OPEN")
            return

        if self.completed_at is None:
            raise ValueError("JOB_COMPLETION_TIME_REQUIRED")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("JOB_COMPLETION_TIME_MUST_BE_TIMEZONE_AWARE")

        canonical_completion = self.completed_at.astimezone(timezone.utc)
        if canonical_completion < self.run.scheduled_for.astimezone(timezone.utc):
            raise ValueError("JOB_COMPLETION_PRECEDES_SCHEDULE")
        object.__setattr__(self, "completed_at", canonical_completion)

        if status is JobRunStatus.SUCCEEDED:
            if self.failure_code is not None:
                raise ValueError("JOB_SUCCESS_CANNOT_HAVE_FAILURE_CODE")
            return

        normalized_failure = (self.failure_code or "").strip()
        if not normalized_failure:
            raise ValueError("JOB_FAILURE_CODE_REQUIRED")
        object.__setattr__(self, "failure_code", normalized_failure)


def create_scheduled_job_run(job_key: str, scheduled_for: datetime) -> ScheduledJobRun:
    """Create a deterministic job-run identity from a name and scheduled instant."""
    normalized_key = job_key.strip()
    if not normalized_key:
        raise ValueError("JOB_KEY_REQUIRED")
    if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
        raise ValueError("JOB_SCHEDULE_TIME_MUST_BE_TIMEZONE_AWARE")

    canonical_time = scheduled_for.astimezone(timezone.utc)
    canonical = f"{normalized_key}|{canonical_time.isoformat()}"
    return ScheduledJobRun(
        job_run_id=uuid5(NAMESPACE_URL, f"hope:scheduled-job:{canonical}"),
        job_key=normalized_key,
        scheduled_for=canonical_time,
    )


def create_job_run_completion(
    job_run: ScheduledJobRun,
    status: JobRunStatus,
    completed_at: datetime,
    *,
    failure_code: str | None = None,
) -> JobRunRecord:
    """Create a validated terminal lifecycle record for a claimed job run."""
    try:
        normalized_status = JobRunStatus(status)
    except ValueError as exc:
        raise ValueError("JOB_RUN_STATUS_INVALID") from exc
    if normalized_status is JobRunStatus.CLAIMED:
        raise ValueError("JOB_COMPLETION_STATUS_REQUIRED")
    return JobRunRecord(
        run=job_run,
        status=normalized_status,
        completed_at=completed_at,
        failure_code=failure_code,
    )
