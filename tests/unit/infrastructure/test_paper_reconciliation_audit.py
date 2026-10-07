from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from hope.application.jobs import (
    JobRunStatus,
    create_job_run_completion,
    create_scheduled_job_run,
)
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.infrastructure.repositories.paper_reconciliation_audit import (
    SqlAlchemyPaperReconciliationAuditRepository,
)


class _InsertedResult:
    def __init__(self, inserted_id):
        self._inserted_id = inserted_id

    def scalar_one_or_none(self):
        return self._inserted_id


class _Connection:
    def execute(self, statement):
        return _InsertedResult(uuid4())


def test_new_reconciliation_receipt_must_be_reread_before_record_succeeds(
    monkeypatch,
) -> None:
    repository = SqlAlchemyPaperReconciliationAuditRepository(_Connection())
    monkeypatch.setattr(
        repository,
        "_expected",
        lambda completion, effects: (uuid4(), "job-run", {}),
    )
    verify_calls = []

    def _not_durable(completion, effects):
        verify_calls.append((completion, effects))
        return False

    monkeypatch.setattr(repository, "verify", _not_durable)
    completion = object()
    effects = ()

    with pytest.raises(
        RuntimeError,
        match="PAPER_RECONCILIATION_AUDIT_NOT_DURABLE",
    ):
        repository.record(completion, effects)

    assert verify_calls == [(completion, effects)]


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _ReadConnection:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, statement):
        return _RowsResult(self._rows)


def test_verify_rejects_same_job_receipt_with_different_audit_identity(
    monkeypatch,
) -> None:
    expected_id = uuid4()
    conflicting_id = uuid4()
    repository = SqlAlchemyPaperReconciliationAuditRepository(
        _ReadConnection(
            [
                {
                    "audit_event_id": conflicting_id,
                    "event_type": "PAPER_RUN_RECONCILED",
                    "entity_type": "JOB_RUN",
                    "entity_id": "job-run",
                    "payload": {"schema_version": 1},
                }
            ]
        )
    )
    monkeypatch.setattr(
        repository,
        "_expected",
        lambda completion, effects: (
            expected_id,
            "job-run",
            {"schema_version": 1},
        ),
    )

    with pytest.raises(
        ValueError,
        match="PAPER_RECONCILIATION_AUDIT_IDENTITY_CONFLICT",
    ):
        repository.verify(object(), ())


def _completion_and_effects(
    effect_types: tuple[PaperEffectType, ...],
):
    scheduled_for = datetime(2026, 10, 5, 5, 0, tzinfo=UTC)
    job_run = create_scheduled_job_run(
        "paper:USA:00000000-0000-0000-0000-000000000001:audit-shape",
        scheduled_for,
    )
    completion = create_job_run_completion(
        job_run,
        JobRunStatus.SUCCEEDED,
        scheduled_for + timedelta(minutes=1),
    )
    effects = tuple(
        create_paper_effect(
            job_run,
            effect_type,
            uuid4(),
            "a" * 64,
        )
        for effect_type in effect_types
    )
    return completion, effects


@pytest.mark.parametrize(
    "effect_types",
    [
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.FILL,
            PaperEffectType.PNL,
        ),
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.CANCELLATION,
        ),
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.REJECTION,
        ),
    ],
)
def test_reconciliation_receipt_accepts_only_complete_terminal_shapes(
    effect_types,
) -> None:
    completion, effects = _completion_and_effects(effect_types)
    repository = SqlAlchemyPaperReconciliationAuditRepository(_Connection())

    _, _, payload = repository._expected(completion, effects)

    assert [item["effect_type"] for item in payload["effects"]] == [
        effect_type.value for effect_type in effect_types
    ]


@pytest.mark.parametrize(
    "effect_types",
    [
        (),
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
        ),
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.REJECTION,
        ),
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.CANCELLATION,
            PaperEffectType.REJECTION,
        ),
        # Recovery classifies both of these as contradictory. Reconciliation must
        # reject them through the same canonical topology contract rather than
        # accidentally accepting a locally maintained superset.
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.CANCELLATION,
            PaperEffectType.PNL,
        ),
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.REJECTION,
            PaperEffectType.PNL,
        ),
        # A filled lifecycle is complete only when both FILL and PNL are durable.
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.FILL,
        ),
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.PNL,
        ),
    ],
)
def test_reconciliation_receipt_rejects_incomplete_or_ambiguous_effect_shape(
    effect_types,
) -> None:
    completion, effects = _completion_and_effects(effect_types)
    repository = SqlAlchemyPaperReconciliationAuditRepository(_Connection())

    with pytest.raises(
        ValueError,
        match="PAPER_RECONCILIATION_AUDIT_EFFECT_SHAPE_INVALID",
    ):
        repository._expected(completion, effects)


@pytest.mark.parametrize("failure", ["missing", "conflicting", "owner"])
def test_verify_rejects_receipt_without_exact_recoverable_effect_lineage(
    monkeypatch,
    failure,
) -> None:
    completion, effects = _completion_and_effects(
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.FILL,
            PaperEffectType.PNL,
        )
    )
    repository = SqlAlchemyPaperReconciliationAuditRepository(_ReadConnection([]))
    event_id, entity_id, payload = repository._expected(completion, effects)
    repository._connection._rows = [
        {
            "audit_event_id": event_id,
            "event_type": "PAPER_RUN_RECONCILED",
            "entity_type": "JOB_RUN",
            "entity_id": entity_id,
            "payload": payload,
        }
    ]

    class _UnrecoverableEffects:
        def get_recoverable(self, effect_type, effect_entity_id):
            effect = next(
                item
                for item in effects
                if item.effect_type is effect_type
                and item.entity_id == effect_entity_id
            )
            if failure == "owner":
                raise ValueError("PAPER_EFFECT_OWNER_NOT_RECOVERABLE")
            if failure == "missing":
                return None
            return replace(effect, payload_hash="b" * 64)

    monkeypatch.setattr(
        "hope.infrastructure.repositories.paper_reconciliation_audit."
        "SqlAlchemyPaperEffectRepository",
        lambda connection: _UnrecoverableEffects(),
    )

    with pytest.raises(
        ValueError,
        match="PAPER_RECONCILIATION_AUDIT_EFFECT_NOT_RECOVERABLE",
    ):
        repository.verify(completion, effects)
