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
    record_error = None
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
        if type(self).record_error is not None:
            raise type(self).record_error
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
    _FakeRepository.record_error = None
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


def test_reconciliation_rejects_unvalidated_run_before_database_activity() -> None:
    current = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)
    _FakeRepository.completion = None

    with pytest.raises(
        TypeError,
        match="PAPER_JOB_RECONCILIATION_REQUIRES_SCHEDULED_JOB_RUN",
    ):
        reconcile_completed_paper_run(
            _FakeConnection(),
            object(),
            current=current,
        )

    assert _FakeControlRepository.calls == 0
    assert _FakeConnection.exits == []
    assert _FakeEffectRepository.calls == 0
    assert _FakeRepository.completion is None


@pytest.mark.parametrize(
    ("current", "message"),
    [
        (
            datetime(2026, 10, 5, 2, 0),
            "PAPER_JOB_RECONCILIATION_CURRENT_MUST_BE_TIMEZONE_AWARE",
        ),
        (
            datetime(2026, 10, 5, 1, 59, tzinfo=UTC),
            "PAPER_JOB_RECONCILIATION_CURRENT_PRECEDES_SCHEDULE",
        ),
    ],
)
def test_reconciliation_rejects_invalid_current_before_database_activity(
    current,
    message,
) -> None:
    job_run = create_scheduled_job_run(
        "paper:invalid-reconciliation-current",
        datetime(2026, 10, 5, 2, 0, tzinfo=UTC),
    )
    _FakeRepository.completion = None

    with pytest.raises(ValueError, match=message):
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert _FakeControlRepository.calls == 0
    assert _FakeConnection.exits == []
    assert _FakeEffectRepository.calls == 0
    assert _FakeRepository.completion is None


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


def test_reconciliation_classifies_malformed_lifecycle_before_effect_read(
    monkeypatch,
) -> None:
    current = datetime(2026, 10, 4, 15, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:malformed-lifecycle",
        datetime(2026, 10, 4, 15, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_result = False
    _FakeRepository.record_error = ValueError("JOB_RUN_STATUS_INVALID")
    _FakeRepository.completion = None
    monkeypatch.setattr(
        paper_reconciliation,
        "SqlAlchemyJobRunRepository",
        _FakeRepository,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_LIFECYCLE_MISMATCH",
    ) as error:
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert isinstance(error.value.__cause__, ValueError)
    assert _FakeRepository.completion is None
    assert _FakeEffectRepository.calls == 0
    assert _FakeAuditRepository.calls == []
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



def test_reconciliation_fails_closed_when_accounting_truth_rejects_proven_effects(
    monkeypatch,
) -> None:
    """A proven recovery decision cannot bypass contradictory accounting truth."""
    current = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:accounting-mismatch",
        datetime(2026, 10, 5, 2, 30, tzinfo=UTC),
    )
    effects = (
        SimpleNamespace(effect_type=paper_reconciliation.PaperEffectType.FILL),
        SimpleNamespace(effect_type=paper_reconciliation.PaperEffectType.PNL),
    )
    _FakeEffectRepository.snapshots = (effects, effects)
    _FakeRepository.lock_result = True
    _FakeRepository.completion = None
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
    monkeypatch.setattr(
        paper_reconciliation,
        "_assert_durable_accounting_truth",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("PAPER_JOB_RECONCILIATION_ACCOUNTING_MISMATCH")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_ACCOUNTING_MISMATCH",
    ):
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert _FakeRepository.completion is None
    assert _FakeAuditRepository.calls == []
    assert _FakeAuditRepository.verify_calls == []
    assert _FakeConnection.exits == [RuntimeError]




def test_reconciliation_fails_closed_when_materialized_portfolio_state_is_inconsistent(
    monkeypatch,
) -> None:
    """Portfolio replay mismatch must block terminalization before any audit receipt."""
    current = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:portfolio-state-mismatch",
        datetime(2026, 10, 5, 3, 30, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.completion = None
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
    monkeypatch.setattr(
        paper_reconciliation,
        "_assert_durable_accounting_truth",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("PAPER_JOB_RECONCILIATION_PORTFOLIO_STATE_MISMATCH")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_PORTFOLIO_STATE_MISMATCH",
    ):
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert _FakeRepository.completion is None
    assert _FakeAuditRepository.calls == []
    assert _FakeAuditRepository.verify_calls == []
    assert _FakeConnection.exits == [RuntimeError]





def test_reconciliation_requests_locked_portfolio_replay(monkeypatch) -> None:
    """Accounting verification must hold the portfolio projection lock."""
    from decimal import Decimal

    from hope.application.paper.effects import create_paper_effect
    from hope.application.paper.portfolio_pnl import (
        PaperPortfolioPnLEvent,
        paper_portfolio_pnl_payload_hash,
    )

    portfolio_id = uuid4()
    fill_id = uuid4()
    pnl_id = uuid4()
    instrument_id = uuid4()
    event_time = datetime(2026, 10, 5, 4, 1, tzinfo=UTC)
    pnl_event = PaperPortfolioPnLEvent(
        pnl_id,
        portfolio_id,
        fill_id,
        instrument_id,
        Decimal("12.50"),
        Decimal("0.75"),
        event_time,
    )
    job_run = create_scheduled_job_run(
        "paper:locked-portfolio-replay",
        datetime(2026, 10, 5, 4, 0, tzinfo=UTC),
    )
    effects = (
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.FILL,
            fill_id,
            "f" * 64,
        ),
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.PNL,
            pnl_id,
            paper_portfolio_pnl_payload_hash(pnl_event),
        ),
    )

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return [{
                "fill_id": fill_id,
                "portfolio_id": portfolio_id,
                "pnl_event_id": pnl_id,
                "pnl_fill_id": fill_id,
                "pnl_portfolio_id": portfolio_id,
                "pnl_instrument_id": instrument_id,
                "realized_pnl_delta": Decimal("12.50"),
                "commission_delta": Decimal("0.75"),
                "event_time": event_time,
            }]

    class _AccountingConnection:
        def execute(self, *args, **kwargs):
            return _Rows()

    calls = []

    def _load_ledger(self, requested_portfolio_id, *, lock_for_update=False):
        calls.append((requested_portfolio_id, lock_for_update))
        return object()

    from hope.infrastructure.repositories import paper_portfolio
    monkeypatch.setattr(
        paper_portfolio.SqlAlchemyPaperPortfolioRepository,
        "load_ledger",
        _load_ledger,
    )

    paper_reconciliation._assert_durable_accounting_truth(
        _AccountingConnection(),
        effects,
    )

    assert calls == [(portfolio_id, True)]









def test_reconciliation_fails_closed_on_partial_fill_accounting_effects() -> None:
    """A FILL effect without its PNL effect must never be treated as recoverable accounting."""
    fill_id = uuid4()
    job_run = create_scheduled_job_run(
        "paper:partial-fill-accounting-effects",
        datetime(2026, 10, 5, 4, 0, tzinfo=UTC),
    )
    from hope.application.paper.effects import create_paper_effect
    effects = (
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.FILL,
            fill_id,
            "7" * 64,
        ),
    )

    class _AccountingConnection:
        def execute(self, *args, **kwargs):
            raise AssertionError("partial effects must fail before accounting query")

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_ACCOUNTING_EFFECT_MISMATCH",
    ):
        paper_reconciliation._assert_durable_accounting_truth(
            _AccountingConnection(),
            effects,
        )


def test_reconciliation_fails_closed_on_orphan_pnl_accounting_effects() -> None:
    """A PNL effect without its FILL effect must never be treated as recoverable accounting."""
    pnl_id = uuid4()
    job_run = create_scheduled_job_run(
        "paper:orphan-pnl-accounting-effects",
        datetime(2026, 10, 5, 4, 0, tzinfo=UTC),
    )
    from hope.application.paper.effects import create_paper_effect
    effects = (
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.PNL,
            pnl_id,
            "8" * 64,
        ),
    )

    class _AccountingConnection:
        def execute(self, *args, **kwargs):
            raise AssertionError("orphan PNL must fail before accounting query")

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_ACCOUNTING_EFFECT_MISMATCH",
    ):
        paper_reconciliation._assert_durable_accounting_truth(
            _AccountingConnection(),
            effects,
        )



def test_reconciliation_fails_closed_when_accounting_truth_is_missing(
    monkeypatch,
) -> None:
    """Missing durable accounting truth must fail before portfolio replay."""
    fill_id = uuid4()
    pnl_id = uuid4()
    job_run = create_scheduled_job_run(
        "paper:missing-accounting-truth",
        datetime(2026, 10, 5, 4, 0, tzinfo=UTC),
    )
    from hope.application.paper.effects import create_paper_effect
    effects = (
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.FILL,
            fill_id,
            "5" * 64,
        ),
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.PNL,
            pnl_id,
            "6" * 64,
        ),
    )

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return []

    class _AccountingConnection:
        def execute(self, *args, **kwargs):
            return _Rows()

    from hope.infrastructure.repositories import paper_portfolio

    def _must_not_replay(*args, **kwargs):
        raise AssertionError("missing accounting truth must fail before replay")

    monkeypatch.setattr(
        paper_portfolio.SqlAlchemyPaperPortfolioRepository,
        "load_ledger",
        _must_not_replay,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_ACCOUNTING_NOT_DURABLE",
    ):
        paper_reconciliation._assert_durable_accounting_truth(
            _AccountingConnection(),
            effects,
        )



def test_reconciliation_fails_closed_when_accounting_query_returns_duplicates(
    monkeypatch,
) -> None:
    """Duplicate durable accounting truth must never be accepted as canonical."""
    portfolio_id = uuid4()
    fill_id = uuid4()
    pnl_id = uuid4()
    job_run = create_scheduled_job_run(
        "paper:duplicate-accounting-truth",
        datetime(2026, 10, 5, 4, 0, tzinfo=UTC),
    )
    from hope.application.paper.effects import create_paper_effect
    effects = (
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.FILL,
            fill_id,
            "3" * 64,
        ),
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.PNL,
            pnl_id,
            "4" * 64,
        ),
    )
    durable = {
        "fill_id": fill_id,
        "portfolio_id": portfolio_id,
        "pnl_event_id": pnl_id,
        "pnl_fill_id": fill_id,
        "pnl_portfolio_id": portfolio_id,
    }

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return [durable, durable.copy()]

    class _AccountingConnection:
        def execute(self, *args, **kwargs):
            return _Rows()

    from hope.infrastructure.repositories import paper_portfolio

    def _must_not_replay(*args, **kwargs):
        raise AssertionError("duplicate accounting truth must fail before replay")

    monkeypatch.setattr(
        paper_portfolio.SqlAlchemyPaperPortfolioRepository,
        "load_ledger",
        _must_not_replay,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_ACCOUNTING_NOT_DURABLE",
    ):
        paper_reconciliation._assert_durable_accounting_truth(
            _AccountingConnection(),
            effects,
        )



def test_reconciliation_locks_authoritative_fill_before_portfolio_replay(
    monkeypatch,
) -> None:
    """Accounting truth must lock the authoritative fill row before replay."""
    from decimal import Decimal

    from hope.application.paper.effects import create_paper_effect
    from hope.application.paper.portfolio_pnl import (
        PaperPortfolioPnLEvent,
        paper_portfolio_pnl_payload_hash,
    )

    portfolio_id = uuid4()
    fill_id = uuid4()
    pnl_id = uuid4()
    instrument_id = uuid4()
    event_time = datetime(2026, 10, 5, 4, 1, tzinfo=UTC)
    pnl_event = PaperPortfolioPnLEvent(
        pnl_id,
        portfolio_id,
        fill_id,
        instrument_id,
        Decimal("12.50"),
        Decimal("0.75"),
        event_time,
    )
    job_run = create_scheduled_job_run(
        "paper:locked-fill-accounting",
        datetime(2026, 10, 5, 4, 0, tzinfo=UTC),
    )
    effects = (
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.FILL,
            fill_id,
            "1" * 64,
        ),
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.PNL,
            pnl_id,
            paper_portfolio_pnl_payload_hash(pnl_event),
        ),
    )

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return [{
                "fill_id": fill_id,
                "portfolio_id": portfolio_id,
                "pnl_event_id": pnl_id,
                "pnl_fill_id": fill_id,
                "pnl_portfolio_id": portfolio_id,
                "pnl_instrument_id": instrument_id,
                "realized_pnl_delta": Decimal("12.50"),
                "commission_delta": Decimal("0.75"),
                "event_time": event_time,
            }]

    class _AccountingConnection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))
            return _Rows()

    connection = _AccountingConnection()

    from hope.infrastructure.repositories import paper_portfolio
    monkeypatch.setattr(
        paper_portfolio.SqlAlchemyPaperPortfolioRepository,
        "load_ledger",
        lambda self, requested_portfolio_id, *, lock_for_update=False: object(),
    )

    paper_reconciliation._assert_durable_accounting_truth(connection, effects)

    assert len(connection.calls) == 1
    statement, params = connection.calls[0]
    assert "WHERE f.fill_id = :fill_id" in statement
    assert "FOR UPDATE OF f" in statement
    assert params == {"fill_id": fill_id}



def test_reconciliation_translates_locked_portfolio_replay_mismatch(
    monkeypatch,
) -> None:
    """Materialized portfolio corruption must surface as reconciliation-specific failure."""
    portfolio_id = uuid4()
    fill_id = uuid4()
    pnl_id = uuid4()
    job_run = create_scheduled_job_run(
        "paper:locked-portfolio-mismatch",
        datetime(2026, 10, 5, 4, 0, tzinfo=UTC),
    )
    from hope.application.paper.effects import create_paper_effect
    effects = (
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.FILL,
            fill_id,
            "d" * 64,
        ),
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.PNL,
            pnl_id,
            "e" * 64,
        ),
    )

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return [{
                "fill_id": fill_id,
                "portfolio_id": portfolio_id,
                "pnl_event_id": pnl_id,
                "pnl_fill_id": fill_id,
                "pnl_portfolio_id": portfolio_id,
            }]

    class _AccountingConnection:
        def execute(self, *args, **kwargs):
            return _Rows()

    calls = []

    def _load_ledger(self, requested_portfolio_id, *, lock_for_update=False):
        calls.append((requested_portfolio_id, lock_for_update))
        raise RuntimeError("PAPER_PORTFOLIO_MATERIALIZED_STATE_INCONSISTENT")

    from hope.infrastructure.repositories import paper_portfolio
    monkeypatch.setattr(
        paper_portfolio.SqlAlchemyPaperPortfolioRepository,
        "load_ledger",
        _load_ledger,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_PORTFOLIO_STATE_MISMATCH",
    ) as error:
        paper_reconciliation._assert_durable_accounting_truth(
            _AccountingConnection(),
            effects,
        )

    assert calls == [(portfolio_id, True)]
    assert isinstance(error.value.__cause__, RuntimeError)
    assert str(error.value.__cause__) == "PAPER_PORTFOLIO_MATERIALIZED_STATE_INCONSISTENT"



def test_reconciliation_fails_closed_when_locked_portfolio_replay_is_missing(
    monkeypatch,
) -> None:
    """A missing durable portfolio must fail closed even after accounting linkage matches."""
    portfolio_id = uuid4()
    fill_id = uuid4()
    pnl_id = uuid4()
    job_run = create_scheduled_job_run(
        "paper:missing-locked-portfolio",
        datetime(2026, 10, 5, 4, 0, tzinfo=UTC),
    )
    from hope.application.paper.effects import create_paper_effect
    effects = (
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.FILL,
            fill_id,
            "b" * 64,
        ),
        create_paper_effect(
            job_run,
            paper_reconciliation.PaperEffectType.PNL,
            pnl_id,
            "c" * 64,
        ),
    )

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return [{
                "fill_id": fill_id,
                "portfolio_id": portfolio_id,
                "pnl_event_id": pnl_id,
                "pnl_fill_id": fill_id,
                "pnl_portfolio_id": portfolio_id,
            }]

    class _AccountingConnection:
        def execute(self, *args, **kwargs):
            return _Rows()

    calls = []

    def _load_ledger(self, requested_portfolio_id, *, lock_for_update=False):
        calls.append((requested_portfolio_id, lock_for_update))
        return None

    from hope.infrastructure.repositories import paper_portfolio
    monkeypatch.setattr(
        paper_portfolio.SqlAlchemyPaperPortfolioRepository,
        "load_ledger",
        _load_ledger,
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_PORTFOLIO_STATE_NOT_DURABLE",
    ):
        paper_reconciliation._assert_durable_accounting_truth(
            _AccountingConnection(),
            effects,
        )

    assert calls == [(portfolio_id, True)]



def test_reconciliation_database_error_rolls_back_before_terminalization(
    monkeypatch,
) -> None:
    """Database failure while authenticating accounting must never terminalize."""
    current = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:accounting-database-failure",
        datetime(2026, 10, 5, 4, 0, tzinfo=UTC),
    )
    _FakeRepository.lock_result = True
    _FakeRepository.completion = None
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
    monkeypatch.setattr(
        paper_reconciliation,
        "_assert_durable_accounting_truth",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            paper_reconciliation.SQLAlchemyError("database unavailable")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="PAPER_JOB_RECONCILIATION_DATABASE_UNAVAILABLE",
    ):
        reconcile_completed_paper_run(
            _FakeConnection(),
            job_run,
            current=current,
        )

    assert _FakeRepository.completion is None
    assert _FakeAuditRepository.calls == []
    assert _FakeAuditRepository.verify_calls == []
    assert _FakeConnection.exits == [paper_reconciliation.SQLAlchemyError]



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



def test_reconciliation_rejects_tampered_durable_pnl_economics(monkeypatch) -> None:
    """Durable PNL economics must authenticate against the committed PNL effect."""
    from decimal import Decimal
    from hope.application.paper.effects import create_paper_effect
    from hope.application.paper.portfolio_pnl import PaperPortfolioPnLEvent, paper_portfolio_pnl_payload_hash

    portfolio_id = uuid4()
    fill_id = uuid4()
    instrument_id = uuid4()
    job_run = create_scheduled_job_run("paper:tampered-pnl-economics", datetime(2026, 10, 5, 4, 0, tzinfo=UTC))
    event_time = datetime(2026, 10, 5, 4, 1, tzinfo=UTC)
    from hope.application.paper.portfolio_pnl import paper_portfolio_pnl_event_id
    pnl_id = paper_portfolio_pnl_event_id(portfolio_id, fill_id)
    original = PaperPortfolioPnLEvent(pnl_id, portfolio_id, fill_id, instrument_id, Decimal("12.50"), Decimal("0.75"), event_time)
    effects = (
        create_paper_effect(job_run, paper_reconciliation.PaperEffectType.FILL, fill_id, "9" * 64),
        create_paper_effect(job_run, paper_reconciliation.PaperEffectType.PNL, pnl_id, paper_portfolio_pnl_payload_hash(original)),
    )
    durable = {
        "fill_id": fill_id, "portfolio_id": portfolio_id, "pnl_event_id": pnl_id,
        "pnl_fill_id": fill_id, "pnl_portfolio_id": portfolio_id,
        "pnl_instrument_id": instrument_id, "realized_pnl_delta": Decimal("99.99"),
        "commission_delta": Decimal("0.75"), "event_time": event_time,
    }
    class _Rows:
        def mappings(self): return self
        def all(self): return [durable]
    class _Connection:
        def execute(self, *args, **kwargs): return _Rows()
    from hope.infrastructure.repositories import paper_portfolio
    monkeypatch.setattr(paper_portfolio.SqlAlchemyPaperPortfolioRepository, "load_ledger", lambda *args, **kwargs: object())
    with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_PNL_STATE_MISMATCH"):
        paper_reconciliation._assert_durable_accounting_truth(_Connection(), effects)


def test_reconciliation_rejects_tampered_pnl_commission(monkeypatch) -> None:
    """Commission corruption must invalidate the committed PNL effect."""
    from decimal import Decimal
    from hope.application.paper.effects import create_paper_effect
    from hope.application.paper.portfolio_pnl import PaperPortfolioPnLEvent, paper_portfolio_pnl_event_id, paper_portfolio_pnl_payload_hash
    portfolio_id, fill_id, instrument_id = uuid4(), uuid4(), uuid4()
    job_run = create_scheduled_job_run("paper:tampered-pnl-commission", datetime(2026, 10, 5, 4, 0, tzinfo=UTC))
    event_time = datetime(2026, 10, 5, 4, 1, tzinfo=UTC)
    pnl_id = paper_portfolio_pnl_event_id(portfolio_id, fill_id)
    original = PaperPortfolioPnLEvent(pnl_id, portfolio_id, fill_id, instrument_id, Decimal("12.50"), Decimal("0.75"), event_time)
    effects = (create_paper_effect(job_run, paper_reconciliation.PaperEffectType.FILL, fill_id, "7"*64), create_paper_effect(job_run, paper_reconciliation.PaperEffectType.PNL, pnl_id, paper_portfolio_pnl_payload_hash(original)))
    durable = {"fill_id":fill_id,"portfolio_id":portfolio_id,"pnl_event_id":pnl_id,"pnl_fill_id":fill_id,"pnl_portfolio_id":portfolio_id,"pnl_instrument_id":instrument_id,"realized_pnl_delta":Decimal("12.50"),"commission_delta":Decimal("9.99"),"event_time":event_time}
    class R:
        def mappings(self): return self
        def all(self): return [durable]
    class C:
        def execute(self,*a,**k): return R()
    from hope.infrastructure.repositories import paper_portfolio
    monkeypatch.setattr(paper_portfolio.SqlAlchemyPaperPortfolioRepository,"load_ledger",lambda *a,**k: object())
    with pytest.raises(RuntimeError, match="PAPER_JOB_RECONCILIATION_PNL_STATE_MISMATCH"):
        paper_reconciliation._assert_durable_accounting_truth(C(), effects)


def test_reconciliation_rejects_tampered_pnl_instrument(monkeypatch) -> None:
    """Instrument corruption must invalidate the committed PNL effect."""
    from decimal import Decimal
    from hope.application.paper.effects import create_paper_effect
    from hope.application.paper.portfolio_pnl import PaperPortfolioPnLEvent, paper_portfolio_pnl_event_id, paper_portfolio_pnl_payload_hash
    portfolio_id, fill_id, instrument_id = uuid4(), uuid4(), uuid4()
    job_run = create_scheduled_job_run("paper:tampered-pnl-instrument", datetime(2026,10,5,4,0,tzinfo=UTC))
    event_time = datetime(2026,10,5,4,1,tzinfo=UTC)
    pnl_id = paper_portfolio_pnl_event_id(portfolio_id, fill_id)
    original = PaperPortfolioPnLEvent(pnl_id,portfolio_id,fill_id,instrument_id,Decimal("12.50"),Decimal("0.75"),event_time)
    effects=(create_paper_effect(job_run,paper_reconciliation.PaperEffectType.FILL,fill_id,"5"*64),create_paper_effect(job_run,paper_reconciliation.PaperEffectType.PNL,pnl_id,paper_portfolio_pnl_payload_hash(original)))
    durable={"fill_id":fill_id,"portfolio_id":portfolio_id,"pnl_event_id":pnl_id,"pnl_fill_id":fill_id,"pnl_portfolio_id":portfolio_id,"pnl_instrument_id":uuid4(),"realized_pnl_delta":Decimal("12.50"),"commission_delta":Decimal("0.75"),"event_time":event_time}
    class R:
        def mappings(self): return self
        def all(self): return [durable]
    class C:
        def execute(self,*a,**k): return R()
    from hope.infrastructure.repositories import paper_portfolio
    monkeypatch.setattr(paper_portfolio.SqlAlchemyPaperPortfolioRepository,"load_ledger",lambda *a,**k: object())
    with pytest.raises(RuntimeError,match="PAPER_JOB_RECONCILIATION_PNL_STATE_MISMATCH"):
        paper_reconciliation._assert_durable_accounting_truth(C(),effects)


def test_reconciliation_rejects_tampered_pnl_event_time(monkeypatch) -> None:
    """Event-time corruption must invalidate the committed PNL effect."""
    from decimal import Decimal
    from datetime import timedelta
    from hope.application.paper.effects import create_paper_effect
    from hope.application.paper.portfolio_pnl import PaperPortfolioPnLEvent, paper_portfolio_pnl_event_id, paper_portfolio_pnl_payload_hash
    portfolio_id, fill_id, instrument_id = uuid4(), uuid4(), uuid4()
    job_run=create_scheduled_job_run("paper:tampered-pnl-time",datetime(2026,10,5,4,0,tzinfo=UTC))
    event_time=datetime(2026,10,5,4,1,tzinfo=UTC)
    pnl_id=paper_portfolio_pnl_event_id(portfolio_id,fill_id)
    original=PaperPortfolioPnLEvent(pnl_id,portfolio_id,fill_id,instrument_id,Decimal("12.50"),Decimal("0.75"),event_time)
    effects=(create_paper_effect(job_run,paper_reconciliation.PaperEffectType.FILL,fill_id,"6"*64),create_paper_effect(job_run,paper_reconciliation.PaperEffectType.PNL,pnl_id,paper_portfolio_pnl_payload_hash(original)))
    durable={"fill_id":fill_id,"portfolio_id":portfolio_id,"pnl_event_id":pnl_id,"pnl_fill_id":fill_id,"pnl_portfolio_id":portfolio_id,"pnl_instrument_id":instrument_id,"realized_pnl_delta":Decimal("12.50"),"commission_delta":Decimal("0.75"),"event_time":event_time+timedelta(seconds=1)}
    class R:
        def mappings(self): return self
        def all(self): return [durable]
    class C:
        def execute(self,*a,**k): return R()
    from hope.infrastructure.repositories import paper_portfolio
    monkeypatch.setattr(paper_portfolio.SqlAlchemyPaperPortfolioRepository,"load_ledger",lambda *a,**k: object())
    with pytest.raises(RuntimeError,match="PAPER_JOB_RECONCILIATION_PNL_STATE_MISMATCH"):
        paper_reconciliation._assert_durable_accounting_truth(C(),effects)
