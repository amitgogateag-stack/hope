from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hope.application.jobs import JobRunRecord, JobRunStatus, create_scheduled_job_run
from hope.application.paper import (
    PaperCycleOutcome,
    PaperCycleRunner,
    PaperFillAccountingWriter,
    PaperOrderWriter,
    PaperSignalWriter,
)


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


class RecordingSignalWriter(PaperSignalWriter):
    def __init__(self) -> None:
        self.calls = []

    def record(self, context, signal) -> bool:
        self.calls.append((context, signal))
        return True


class RecordingOrderWriter(PaperOrderWriter):
    def __init__(self) -> None:
        self.calls = []

    def record(self, context, order) -> bool:
        self.calls.append((context, order))
        return True


class RecordingFillAccountingWriter(PaperFillAccountingWriter):
    def __init__(self) -> None:
        self.calls = []

    def record(self, context, portfolio_id, initial_cash, fill, *, sequence: int) -> bool:
        self.calls.append((context, portfolio_id, initial_cash, fill, sequence))
        return True


def make_run():
    return create_scheduled_job_run(
        "paper-cycle",
        datetime(2026, 9, 9, 20, 0, tzinfo=UTC),
    )


def test_paper_cycle_runner_has_no_public_arbitrary_callback_entrypoint() -> None:
    assert not hasattr(PaperCycleRunner, "run")


def test_paper_cycle_runner_executes_claimed_run_and_marks_success() -> None:
    job_run = make_run()
    repository = FakeJobRunRepository()
    calls = []
    runner = PaperCycleRunner(
        repository,
        now=lambda: job_run.scheduled_for + timedelta(minutes=1),
    )

    outcome = runner._run_claimed_cycle(job_run, lambda context: calls.append(context.job_run.job_run_id))

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

    outcome = runner._run_claimed_cycle(job_run, lambda context: pytest.fail("work must not run"))

    assert outcome is PaperCycleOutcome.SKIPPED_TERMINAL
    assert repository.completions == []


def test_paper_cycle_runner_rejects_incomplete_prior_claim() -> None:
    job_run = make_run()
    claimed = JobRunRecord(run=job_run, status=JobRunStatus.CLAIMED)
    repository = FakeJobRunRepository(claim_result=False, record=claimed)
    runner = PaperCycleRunner(repository, now=lambda: job_run.scheduled_for)

    with pytest.raises(RuntimeError, match="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"):
        runner._run_claimed_cycle(job_run, lambda context: pytest.fail("work must not run"))


def test_paper_cycle_runner_marks_failure_and_reraises_work_error() -> None:
    job_run = make_run()
    repository = FakeJobRunRepository()
    runner = PaperCycleRunner(
        repository,
        now=lambda: job_run.scheduled_for + timedelta(minutes=2),
    )

    def fail(_context) -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        runner._run_claimed_cycle(job_run, fail)

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
        runner._run_claimed_cycle(job_run, lambda context: None)


def test_paper_cycle_runner_rejects_missing_state_after_failed_claim() -> None:
    job_run = make_run()
    repository = FakeJobRunRepository(claim_result=False, record=None)
    runner = PaperCycleRunner(repository, now=lambda: job_run.scheduled_for)

    with pytest.raises(RuntimeError, match="PAPER_JOB_CLAIM_STATE_MISSING"):
        runner._run_claimed_cycle(job_run, lambda context: pytest.fail("work must not run"))


def test_paper_cycle_runtime_facade_routes_each_effect_through_authoritative_writer() -> None:
    job_run = make_run()
    repository = FakeJobRunRepository()
    signal_writer = RecordingSignalWriter()
    order_writer = RecordingOrderWriter()
    fill_writer = RecordingFillAccountingWriter()
    runner = PaperCycleRunner(
        repository,
        now=lambda: job_run.scheduled_for + timedelta(minutes=1),
    )
    portfolio_id = UUID(int=1)
    signal = object()
    order = object()
    fill = object()

    def work(runtime) -> None:
        assert runtime.record_signal(signal) is True
        assert runtime.record_order(order) is True
        assert runtime.record_fill(
            portfolio_id,
            Decimal("1000"),
            fill,
            sequence=7,
        ) is True

    outcome = runner.run_runtime(
        job_run,
        signal_writer,
        order_writer,
        fill_writer,
        work,
    )

    assert outcome is PaperCycleOutcome.EXECUTED
    assert signal_writer.calls == [(signal_writer.calls[0][0], signal)]
    assert order_writer.calls == [(order_writer.calls[0][0], order)]
    assert len(fill_writer.calls) == 1
    signal_context = signal_writer.calls[0][0]
    order_context = order_writer.calls[0][0]
    fill_context, actual_portfolio_id, initial_cash, actual_fill, sequence = fill_writer.calls[0]
    assert signal_context is order_context is fill_context
    assert signal_context.job_run.job_run_id == job_run.job_run_id
    assert actual_portfolio_id == portfolio_id
    assert initial_cash == Decimal("1000")
    assert actual_fill is fill
    assert sequence == 7


@pytest.mark.parametrize(
    ("writers", "message"),
    [
        ((object(), RecordingOrderWriter(), RecordingFillAccountingWriter()), "PAPER_RUNTIME_REQUIRES_AUTHORITATIVE_SIGNAL_WRITER"),
        ((RecordingSignalWriter(), object(), RecordingFillAccountingWriter()), "PAPER_RUNTIME_REQUIRES_AUTHORITATIVE_ORDER_WRITER"),
        ((RecordingSignalWriter(), RecordingOrderWriter(), object()), "PAPER_RUNTIME_REQUIRES_AUTHORITATIVE_FILL_WRITER"),
    ],
)
def test_paper_cycle_runtime_facade_rejects_non_authoritative_writers(writers, message) -> None:
    job_run = make_run()
    repository = FakeJobRunRepository()
    runner = PaperCycleRunner(repository, now=lambda: job_run.scheduled_for)

    with pytest.raises(TypeError, match=message):
        runner.run_runtime(job_run, *writers, lambda runtime: None)

    assert repository.completions == []


def test_quarantine_incomplete_claim_marks_stranded_run_failed() -> None:
    job_run = make_run()
    claimed = JobRunRecord(run=job_run, status=JobRunStatus.CLAIMED)
    repository = FakeJobRunRepository(record=claimed)
    runner = PaperCycleRunner(
        repository,
        now=lambda: job_run.scheduled_for + timedelta(minutes=5),
    )

    outcome = runner.quarantine_incomplete_claim(job_run)

    assert outcome is PaperCycleOutcome.QUARANTINED_INCOMPLETE
    assert len(repository.completions) == 1
    completion = repository.completions[0]
    assert completion.status is JobRunStatus.FAILED
    assert completion.failure_code == "PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"


def test_quarantine_incomplete_claim_is_idempotent_for_terminal_run() -> None:
    job_run = make_run()
    terminal = JobRunRecord(
        run=job_run,
        status=JobRunStatus.FAILED,
        completed_at=job_run.scheduled_for + timedelta(minutes=1),
        failure_code="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM",
    )
    repository = FakeJobRunRepository(record=terminal)
    runner = PaperCycleRunner(repository, now=lambda: terminal.completed_at)

    assert runner.quarantine_incomplete_claim(job_run) is PaperCycleOutcome.SKIPPED_TERMINAL
    assert repository.completions == []


def test_quarantine_incomplete_claim_rejects_missing_state() -> None:
    job_run = make_run()
    repository = FakeJobRunRepository(record=None)
    runner = PaperCycleRunner(repository, now=lambda: job_run.scheduled_for)

    with pytest.raises(RuntimeError, match="PAPER_JOB_CLAIM_STATE_MISSING"):
        runner.quarantine_incomplete_claim(job_run)


def test_quarantine_incomplete_claim_fails_closed_when_terminal_write_loses() -> None:
    job_run = make_run()
    claimed = JobRunRecord(run=job_run, status=JobRunStatus.CLAIMED)
    repository = FakeJobRunRepository(record=claimed, complete_result=False)
    runner = PaperCycleRunner(
        repository,
        now=lambda: job_run.scheduled_for + timedelta(minutes=5),
    )

    with pytest.raises(RuntimeError, match="PAPER_JOB_COMPLETION_CONFLICT"):
        runner.quarantine_incomplete_claim(job_run)
