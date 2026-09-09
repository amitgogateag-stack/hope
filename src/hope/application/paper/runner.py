from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Callable, Protocol

from hope.application.jobs import (
    JobRunRecord,
    JobRunStatus,
    ScheduledJobRun,
    create_job_run_completion,
)


class PaperCycleOutcome(str, Enum):
    EXECUTED = "EXECUTED"
    SKIPPED_TERMINAL = "SKIPPED_TERMINAL"
    QUARANTINED_INCOMPLETE = "QUARANTINED_INCOMPLETE"


class PaperJobRunRepository(Protocol):
    def claim(self, job_run: ScheduledJobRun) -> bool:
        ...

    def complete(self, completion: JobRunRecord) -> bool:
        ...

    def get_record(self, job_run_id) -> JobRunRecord | None:
        ...


class PaperCycleRunner:
    """Execute one scheduled PAPER cycle behind the durable job-run boundary."""

    def __init__(
        self,
        repository: PaperJobRunRepository,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._now = now

    def run(
        self,
        job_run: ScheduledJobRun,
        work: Callable[[ScheduledJobRun], None],
    ) -> PaperCycleOutcome:
        if not self._repository.claim(job_run):
            record = self._repository.get_record(job_run.job_run_id)
            if record is None:
                raise RuntimeError("PAPER_JOB_CLAIM_STATE_MISSING")
            if record.status is JobRunStatus.CLAIMED:
                raise RuntimeError("PAPER_JOB_INCOMPLETE_PRIOR_CLAIM")
            return PaperCycleOutcome.SKIPPED_TERMINAL

        try:
            work(job_run)
        except Exception as exc:
            completion = create_job_run_completion(
                job_run,
                JobRunStatus.FAILED,
                self._now(),
                failure_code=type(exc).__name__,
            )
            if not self._repository.complete(completion):
                raise RuntimeError("PAPER_JOB_COMPLETION_CONFLICT") from exc
            raise

        completion = create_job_run_completion(
            job_run,
            JobRunStatus.SUCCEEDED,
            self._now(),
        )
        if not self._repository.complete(completion):
            raise RuntimeError("PAPER_JOB_COMPLETION_CONFLICT")
        return PaperCycleOutcome.EXECUTED

    def quarantine_incomplete_claim(self, job_run: ScheduledJobRun) -> PaperCycleOutcome:
        """Explicitly terminalize a stranded claim after the caller proves its worker is gone."""
        record = self._repository.get_record(job_run.job_run_id)
        if record is None:
            raise RuntimeError("PAPER_JOB_CLAIM_STATE_MISSING")
        if record.status is not JobRunStatus.CLAIMED:
            return PaperCycleOutcome.SKIPPED_TERMINAL

        completion = create_job_run_completion(
            job_run,
            JobRunStatus.FAILED,
            self._now(),
            failure_code="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM",
        )
        if not self._repository.complete(completion):
            raise RuntimeError("PAPER_JOB_COMPLETION_CONFLICT")
        return PaperCycleOutcome.QUARANTINED_INCOMPLETE
