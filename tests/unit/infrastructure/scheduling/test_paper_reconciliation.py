from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.infrastructure.scheduling import paper_reconciliation
from hope.infrastructure.scheduling.paper_reconciliation import reconcile_completed_paper_run
from hope.infrastructure.scheduling.recovery import PaperRecoveryDecision


class _FakeRepository:
    record = None
    lock_result = True
    transition_result = True
    completion = None

    def __init__(self, connection):
        self.connection = connection

    def lock_claimed_for_reconciliation(self, job_run):
        return type(self).lock_result

    def complete(self, completion):
        type(self).completion = completion
        return type(self).transition_result

    def get_record_for_run(self, job_run):
        return type(self).record


def _report(decision):
    return SimpleNamespace(assessments=(SimpleNamespace(decision=decision),))


def test_reconciliation_terminalizes_proven_run_without_replaying_effects(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-reconcile", datetime(2026, 10, 2, 14, 0, tzinfo=UTC))
    _FakeRepository.lock_result = True
    _FakeRepository.transition_result = True
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.SUCCEEDED,
        completed_at=current,
        failure_code=None,
    )
    monkeypatch.setattr(
        paper_reconciliation,
        "assess_due_paper_recovery",
        lambda *args, **kwargs: _report(PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    assert reconcile_completed_paper_run(object(), job_run, current=current) is True
    assert _FakeRepository.completion.run == job_run
    assert _FakeRepository.completion.status is JobRunStatus.SUCCEEDED
    assert _FakeRepository.completion.completed_at == current
    assert _FakeRepository.completion.failure_code is None


def test_reconciliation_rejects_unproven_run_before_lifecycle_mutation(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-unproven", datetime(2026, 10, 2, 14, 0, tzinfo=UTC))
    _FakeRepository.lock_result = True
    _FakeRepository.completion = None
    monkeypatch.setattr(
        paper_reconciliation,
        "assess_due_paper_recovery",
        lambda *args, **kwargs: _report(PaperRecoveryDecision.REQUIRE_RECONCILIATION),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_NOT_PROVEN"):
        reconcile_completed_paper_run(object(), job_run, current=current)
    assert _FakeRepository.completion is None


def test_reconciliation_fails_closed_when_locked_transition_is_not_applied(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper-transition-lost",
        datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.transition_result = False
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.SUCCEEDED,
        completed_at=current,
        failure_code=None,
    )
    monkeypatch.setattr(
        paper_reconciliation,
        "assess_due_paper_recovery",
        lambda *args, **kwargs: _report(PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_TRANSITION_NOT_APPLIED",
    ):
        reconcile_completed_paper_run(object(), job_run, current=current)


def test_reconciliation_is_idempotent_when_race_already_terminalized_success(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-race", datetime(2026, 10, 2, 14, 0, tzinfo=UTC))
    _FakeRepository.lock_result = False
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.SUCCEEDED,
        completed_at=current,
        failure_code=None,
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    assert reconcile_completed_paper_run(object(), job_run, current=current) is False


def test_reconciliation_fails_closed_when_locked_state_is_not_claimed_or_success(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper-mismatch", datetime(2026, 10, 2, 14, 0, tzinfo=UTC))
    _FakeRepository.lock_result = False
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.FAILED,
        completed_at=current,
        failure_code="OTHER_TERMINAL",
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_NOT_CLAIMED"):
        reconcile_completed_paper_run(object(), job_run, current=current)
