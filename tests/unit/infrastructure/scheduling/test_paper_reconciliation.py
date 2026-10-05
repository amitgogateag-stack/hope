from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.infrastructure.scheduling import paper_reconciliation
from hope.infrastructure.scheduling.paper_reconciliation import reconcile_completed_paper_run
from hope.infrastructure.scheduling.recovery import PaperRecoveryDecision


class _FakeConnection:
    exits = []

    class _Savepoint:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            _FakeConnection.exits.append(exc_type)
            return False

    def begin_nested(self):
        return self._Savepoint()


class _FakeControlRepository:
    calls = 0
    error = None

    def __init__(self, connection):
        self.connection = connection

    def assert_running(self):
        type(self).calls += 1
        if type(self).error is not None:
            raise type(self).error


class _FakeRepository:
    record = None
    lock_error = None
    lock_result = True
    transition_result = True
    completion = None

    def __init__(self, connection):
        self.connection = connection

    def lock_claimed_for_reconciliation(self, job_run):
        if type(self).lock_error is not None:
            raise type(self).lock_error
        return type(self).lock_result

    def complete(self, completion):
        type(self).completion = completion
        return type(self).transition_result

    def get_record_for_run(self, job_run):
        return type(self).record


class _FakeEffectRepository:
    snapshots = ((), ())
    calls = 0
    error = None

    def __init__(self, connection):
        self.connection = connection

    def list_for_job_run(self, job_run_id):
        index = min(type(self).calls, len(type(self).snapshots) - 1)
        type(self).calls += 1
        if type(self).error is not None:
            raise type(self).error
        return type(self).snapshots[index]


class _FakeAuditRepository:
    result = True
    record_error = None
    calls = []
    verify_result = True
    verify_calls = []

    def __init__(self, connection):
        self.connection = connection

    def record(self, completion, effects):
        type(self).calls.append((completion, effects))
        if type(self).record_error is not None:
            raise type(self).record_error
        return type(self).result

    def verify(self, completion, effects):
        type(self).verify_calls.append((completion, effects))
        return type(self).verify_result


@pytest.fixture(autouse=True)
def _reset_effect_repository(monkeypatch):
    _FakeConnection.exits = []
    _FakeControlRepository.calls = 0
    _FakeControlRepository.error = None
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyPaperEnvironmentControlRepository",
        _FakeControlRepository,
    )
    _FakeRepository.lock_error = None
    _FakeEffectRepository.snapshots = ((), ())
    _FakeEffectRepository.calls = 0
    _FakeEffectRepository.error = None
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyPaperEffectRepository",
        _FakeEffectRepository,
    )
    _FakeAuditRepository.result = True
    _FakeAuditRepository.record_error = None
    _FakeAuditRepository.calls = []
    _FakeAuditRepository.verify_result = True
    _FakeAuditRepository.verify_calls = []
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyPaperReconciliationAuditRepository",
        _FakeAuditRepository,
    )


def _report(decision, job_run_id):
    return SimpleNamespace(
        assessments=(SimpleNamespace(decision=decision, job_run_id=job_run_id),)
    )


def test_reconciliation_rejects_non_paper_run_before_database_activity() -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "research:reconciliation-forbidden",
        datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
    )
    _FakeRepository.completion = None

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_REQUIRES_PAPER_JOB",
    ):
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert _FakeConnection.exits == []
    assert _FakeEffectRepository.calls == 0
    assert _FakeRepository.completion is None


def test_reconciliation_rejects_halted_environment_before_durable_access(
    monkeypatch,
) -> None:
    current = datetime(2026, 10, 4, 21, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:halted-reconciliation",
        datetime(2026, 10, 4, 20, 30, tzinfo=UTC),
    )
    _FakeControlRepository.error = RuntimeError("PAPER_ENVIRONMENT_HALTED")
    _FakeRepository.completion = None
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyJobRunRepository",
        _FakeRepository,
    )

    with pytest.raises(RuntimeError, match="PAPER_ENVIRONMENT_HALTED"):
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert _FakeControlRepository.calls == 1
    assert _FakeEffectRepository.calls == 0
    assert _FakeRepository.completion is None
    assert _FakeConnection.exits == [RuntimeError]


def test_reconciliation_terminalizes_proven_run_without_replaying_effects(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper:reconcile", datetime(2026, 10, 2, 14, 0, tzinfo=UTC))
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
        lambda *args, **kwargs: _report(
            PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS,
            job_run.job_run_id,
        ),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    assert reconcile_completed_paper_run(_FakeConnection(), job_run, current=current) is True
    assert _FakeRepository.completion.run == job_run
    assert _FakeRepository.completion.status is JobRunStatus.SUCCEEDED
    assert _FakeRepository.completion.completed_at == current
    assert _FakeRepository.completion.failure_code is None
    assert _FakeEffectRepository.calls == 2
    assert _FakeAuditRepository.calls == [(_FakeRepository.record, ())]
    assert _FakeAuditRepository.verify_calls == [(_FakeRepository.record, ())]
    assert _FakeConnection.exits == [None]


def test_reconciliation_normalizes_job_identity_conflict_before_effect_read(
    monkeypatch,
) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:identity-conflict",
        datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_error = ValueError("JOB_RUN_IDENTITY_CONFLICT")
    _FakeRepository.completion = None
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyJobRunRepository",
        _FakeRepository,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_IDENTITY_MISMATCH",
    ) as error:
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert isinstance(error.value.__cause__, ValueError)
    assert _FakeRepository.completion is None
    assert _FakeEffectRepository.calls == 0
    assert _FakeConnection.exits == [RuntimeError]


def test_reconciliation_maps_database_unavailability_and_rolls_back(
    monkeypatch,
) -> None:
    current = datetime(2026, 10, 4, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:database-unavailable",
        datetime(2026, 10, 4, 14, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.completion = None
    _FakeEffectRepository.error = SQLAlchemyError("effect ledger unavailable")
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyJobRunRepository",
        _FakeRepository,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_DATABASE_UNAVAILABLE",
    ) as error:
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert isinstance(error.value.__cause__, SQLAlchemyError)
    assert _FakeRepository.completion is None
    assert _FakeEffectRepository.calls == 1
    assert _FakeConnection.exits == [SQLAlchemyError]


def test_reconciliation_maps_malformed_effect_evidence_and_rolls_back(
    monkeypatch,
) -> None:
    current = datetime(2026, 10, 4, 15, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:malformed-effect-evidence",
        datetime(2026, 10, 4, 14, 30, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.completion = None
    _FakeEffectRepository.error = ValueError("PAPER_EFFECT_IDENTITY_MISMATCH")
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyJobRunRepository",
        _FakeRepository,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_EFFECT_MISMATCH",
    ) as error:
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert isinstance(error.value.__cause__, ValueError)
    assert _FakeRepository.completion is None
    assert _FakeEffectRepository.calls == 1
    assert _FakeAuditRepository.calls == []
    assert _FakeConnection.exits == [RuntimeError]


def test_reconciliation_rejects_unproven_run_before_lifecycle_mutation(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper:unproven", datetime(2026, 10, 2, 14, 0, tzinfo=UTC))
    _FakeRepository.lock_result = True
    _FakeRepository.completion = None
    monkeypatch.setattr(
        paper_reconciliation,
        "assess_due_paper_recovery",
        lambda *args, **kwargs: _report(
            PaperRecoveryDecision.REQUIRE_RECONCILIATION,
            job_run.job_run_id,
        ),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_NOT_PROVEN"):
        reconcile_completed_paper_run(_FakeConnection(), job_run, current=current)
    assert _FakeRepository.completion is None


def test_reconciliation_fails_closed_when_locked_transition_is_not_applied(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:transition-lost",
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
        lambda *args, **kwargs: _report(
            PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS,
            job_run.job_run_id,
        ),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_TRANSITION_NOT_APPLIED",
    ):
        reconcile_completed_paper_run(_FakeConnection(), job_run, current=current)


def test_reconciliation_is_idempotent_when_race_already_terminalized_success(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper:race", datetime(2026, 10, 2, 14, 0, tzinfo=UTC))
    _FakeRepository.lock_result = False
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.SUCCEEDED,
        completed_at=current,
        failure_code=None,
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    assert reconcile_completed_paper_run(_FakeConnection(), job_run, current=current) is False
    assert _FakeEffectRepository.calls == 1
    assert _FakeAuditRepository.verify_calls == [(_FakeRepository.record, ())]


def test_reconciliation_fails_closed_when_locked_state_is_not_claimed_or_success(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run("paper:mismatch", datetime(2026, 10, 2, 14, 0, tzinfo=UTC))
    _FakeRepository.lock_result = False
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.FAILED,
        completed_at=current,
        failure_code="OTHER_TERMINAL",
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_NOT_CLAIMED"):
        reconcile_completed_paper_run(_FakeConnection(), job_run, current=current)


@pytest.mark.parametrize("assessment_ids", [(), (uuid4(),), (uuid4(), uuid4())])
def test_reconciliation_fails_closed_on_mismatched_recovery_assessment(
    monkeypatch,
    assessment_ids,
) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:assessment-mismatch",
        datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.completion = None
    assessments = tuple(
        SimpleNamespace(
            decision=PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS,
            job_run_id=assessment_id,
        )
        for assessment_id in assessment_ids
    )
    monkeypatch.setattr(
        paper_reconciliation,
        "assess_due_paper_recovery",
        lambda *args, **kwargs: SimpleNamespace(assessments=assessments),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_ASSESSMENT_MISMATCH",
    ):
        reconcile_completed_paper_run(_FakeConnection(), job_run, current=current)
    assert _FakeRepository.completion is None


def test_reconciliation_fails_closed_if_effect_ledger_changes_during_terminalization(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:effects-changed",
        datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
    )
    marker = object()
    _FakeEffectRepository.snapshots = ((marker,), (marker, object()))
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
        lambda *args, **kwargs: _report(
            PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS,
            job_run.job_run_id,
        ),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_EFFECTS_CHANGED"):
        reconcile_completed_paper_run(_FakeConnection(), job_run, current=current)
    assert _FakeAuditRepository.calls == []


def test_reconciliation_fails_closed_if_audit_receipt_is_not_new(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:audit-conflict",
        datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.transition_result = True
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.SUCCEEDED,
        completed_at=current,
        failure_code=None,
    )
    _FakeAuditRepository.result = False
    monkeypatch.setattr(
        paper_reconciliation,
        "assess_due_paper_recovery",
        lambda *args, **kwargs: _report(
            PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS,
            job_run.job_run_id,
        ),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_AUDIT_NOT_RECORDED",
    ):
        reconcile_completed_paper_run(_FakeConnection(), job_run, current=current)

def test_reconciliation_fails_closed_if_new_audit_receipt_is_not_durable(monkeypatch) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:audit-not-durable",
        datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.transition_result = True
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.SUCCEEDED,
        completed_at=current,
        failure_code=None,
    )
    _FakeAuditRepository.verify_result = False
    monkeypatch.setattr(
        paper_reconciliation,
        "assess_due_paper_recovery",
        lambda *args, **kwargs: _report(
            PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS,
            job_run.job_run_id,
        ),
    )
    monkeypatch.setattr(paper_reconciliation, "SqlAlchemyJobRunRepository", _FakeRepository)

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_AUDIT_NOT_DURABLE",
    ):
        reconcile_completed_paper_run(_FakeConnection(), job_run, current=current)
    assert _FakeAuditRepository.calls == [(_FakeRepository.record, ())]
    assert _FakeAuditRepository.verify_calls == [(_FakeRepository.record, ())]
    assert _FakeConnection.exits == [RuntimeError]


def test_reconciliation_maps_database_receipt_rejection_and_rolls_back(
    monkeypatch,
) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:audit-database-rejected",
        datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.transition_result = True
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.SUCCEEDED,
        completed_at=current,
        failure_code=None,
    )
    _FakeAuditRepository.record_error = IntegrityError(
        "INSERT INTO audit_events",
        {},
        Exception("PAPER_RECONCILIATION_AUDIT_PAYLOAD_INVALID"),
    )
    monkeypatch.setattr(
        paper_reconciliation,
        "assess_due_paper_recovery",
        lambda *args, **kwargs: _report(
            PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS,
            job_run.job_run_id,
        ),
    )
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyJobRunRepository",
        _FakeRepository,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_AUDIT_REJECTED",
    ) as error:
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert isinstance(error.value.__cause__, IntegrityError)
    assert _FakeAuditRepository.calls == [(_FakeRepository.record, ())]
    assert _FakeAuditRepository.verify_calls == []
    assert _FakeConnection.exits == [RuntimeError]


def test_reconciliation_maps_audit_record_identity_conflict_to_mismatch(
    monkeypatch,
) -> None:
    current = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:audit-identity-conflict",
        datetime(2026, 10, 2, 14, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.transition_result = True
    _FakeRepository.record = SimpleNamespace(
        status=JobRunStatus.SUCCEEDED,
        completed_at=current,
        failure_code=None,
    )
    _FakeAuditRepository.record_error = ValueError(
        "PAPER_RECONCILIATION_AUDIT_IDENTITY_CONFLICT"
    )
    monkeypatch.setattr(
        paper_reconciliation,
        "assess_due_paper_recovery",
        lambda *args, **kwargs: _report(
            PaperRecoveryDecision.ACKNOWLEDGE_COMPLETE_EFFECTS,
            job_run.job_run_id,
        ),
    )
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyJobRunRepository",
        _FakeRepository,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_AUDIT_MISMATCH",
    ):
        reconcile_completed_paper_run(_FakeConnection(), job_run, current=current)

    assert _FakeAuditRepository.calls == [(_FakeRepository.record, ())]
    assert _FakeAuditRepository.verify_calls == []

