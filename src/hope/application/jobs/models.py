from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5


@dataclass(frozen=True)
class ScheduledJobRun:
    """Durable identity for one scheduled invocation of a named HOPE job."""

    job_run_id: UUID
    job_key: str
    scheduled_for: datetime


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
