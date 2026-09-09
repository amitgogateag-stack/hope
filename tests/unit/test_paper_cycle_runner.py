from datetime import datetime, timedelta, timezone

import pytest

from hope.application.jobs import JobRunRecord, JobRunStatus, create_scheduled_job_run
from hope.application.paper import PaperCycleOutcome, PaperCycleRunner


UTC = timezone.utc


class FakeJobRunRepository:
    def __init__(
        self,
        *,
        claim_result: bool = True,
        record: JobRunRecord | None = None,
        complete_result: bool = True,
    ) -> None:
        self.claim_result = claim_result
        self.record = record
        self.complete_result = complete_result
        self.completions: list[JobRunRecord] = []

    def claim(self, job_run) -> bool:
        return self.claim_result

    def complete(self, completion: JobRunRecord) -> bool:
        self.completions.append(completion)
        return self.complete_result

    def get_record(self, job_run_id) -> JobRunRecord | None:
        return self.record


def make_run():
    return create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 20, 0, tzinfo=UTC),
    )


def test_paper_cycle_runner_executes_claimed_run_and_marks_success() -> None:
    job_run = make_run()
    repository = FakeJobRunRepository()
    calls = []
    runner = PaperCycleRunner(
        repository,
        now=lambda: job_run.scheduled_for + timedelta(minutes=1),
    )

    outcome = runner.run(job_run, lambda run: calls.append(run.job_run_id))

    assert outcome is PaperCycleOutcome.EXECUTED
    assert calls == [job_run.job_run_id]
    assert len(repository.completions) == 1
    assert repository.completions[0].status is JobRunStatus.SUCCEEDED


def test_paper_cycle_runner_skips_terminal_duplicate_without_running_work() -> None:
    job_run = make_run()
    terminal = JobRunRecord(
        run=job_run,
        status=JobRunStatus.SUCCEEDED,
        completed_at=job_run.scheduled_for + timedelta(minutes=1),
    )
    repository = FakeJobRunRepository(claim_result=False, record=terminal)
    runner = PaperCycleRunner(repository, now=lambda: terminal.completed_at)

    outcome = runner.run(job_run, lambda run: pytest.fail("work must not run"))

    assert outcome is PaperCycleOutcome.SKIPPED_TERMINAL
    assert repository.completions == []


def test_paper_cycle_runner_rejects_incomplete_prior_claim() -> None:
    job_run = make_run()
    claimed = JobRunRecord(run=job_run, status=JobRunStatus.CLAIMED)
    repository = FakeJobRunRepository(claim_result=False, record=claimed)
    runner = PaperCycleRunner(repository, now=lambda: job_run.scheduled_for)

    with pytest.raises(RuntimeError, match="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"):
        runner.run(job_run, lambda run: pytest.fail("work must not run"))


def test_paper_cycle_runner_marks_failure_and_reraises_work_error() -> None:
    job_run = make_run()
    repository = FakeJobRunRepository()
    runner = PaperCycleRunner(
        repository,
        now=lambda: job_run.scheduled_for + timedelta(minutes=2),
    )

    def fail(_job_run) -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        runner.run(job_run, fail)

    assert len(repository.completions) == 1
    completion = repository.completions[0]
    assert completion.status is JobRunStatus.FAILED
    assert completion.failure_code == "ValueError"


def test_paper_cycle_runner_fails_closed_when_terminal_write_loses() -> None:
    job_run = make_run()
    repository = FakeJobRunRepository(complete_result=False)
    runner = PaperCycleRunner(
        repository,
        now=lambda: job_run.scheduled_for + timedelta(minutes=1),
    )

    with pytest.raises(RuntimeError, match="PAPER_JOB_COMPLETION_CONFLICT"):
        runner.run(job_run, lambda run: None)


def test_paper_cycle_runner_rejects_missing_state_after_failed_claim() -> None:
    job_run = make_run()
    repository = FakeJobRunRepository(claim_result=False, record=None)
    runner = PaperCycleRunner(repository, now=lambda: job_run.scheduled_for)

    with pytest.raises(RuntimeError, match="PAPER_JOB_CLAIM_STATE_MISSING"):
        runner.run(job_run, lambda run: pytest.fail("work must not run"))
