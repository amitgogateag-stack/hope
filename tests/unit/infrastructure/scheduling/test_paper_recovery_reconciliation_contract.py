from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.infrastructure.repositories.paper_reconciliation_audit import (
    SqlAlchemyPaperReconciliationAuditRepository,
)
from hope.infrastructure.scheduling.recovery import (
    PaperRecoveryEvidence,
    _classify_recovery_evidence,
)


class _Connection:
    def execute(self, statement):  # pragma: no cover - _expected does not execute SQL
        raise AssertionError("canonical receipt validation must not write")


def _effects(job_run, effect_types):
    return tuple(
        create_paper_effect(job_run, effect_type, uuid4(), "a" * 64)
        for effect_type in effect_types
    )


def _completion(job_run):
    return SimpleNamespace(
        run=job_run,
        status=JobRunStatus.SUCCEEDED,
        completed_at=datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
        failure_code=None,
    )


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
def test_recovery_complete_shape_is_receipt_admissible(effect_types) -> None:
    job_run = create_scheduled_job_run(
        "paper:USA:00000000-0000-0000-0000-000000000001:contract",
        datetime(2026, 10, 6, 11, 0, tzinfo=UTC),
    )
    effects = _effects(job_run, effect_types)

    assert _classify_recovery_evidence(
        frozenset(effect.effect_type for effect in effects)
    ) is PaperRecoveryEvidence.COMPLETE

    repository = SqlAlchemyPaperReconciliationAuditRepository(_Connection())
    _, entity_id, payload = repository._expected(_completion(job_run), effects)

    assert entity_id == str(job_run.job_run_id)
    assert tuple(item["effect_type"] for item in payload["effects"]) == tuple(
        effect_type.value for effect_type in effect_types
    )


@pytest.mark.parametrize(
    "effect_types",
    [
        (PaperEffectType.SIGNAL, PaperEffectType.RISK, PaperEffectType.ORDER),
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
        (
            PaperEffectType.SIGNAL,
            PaperEffectType.RISK,
            PaperEffectType.ORDER,
            PaperEffectType.CANCELLATION,
            PaperEffectType.REJECTION,
        ),
    ],
)
def test_recovery_noncomplete_shape_is_receipt_inadmissible(effect_types) -> None:
    job_run = create_scheduled_job_run(
        "paper:USA:00000000-0000-0000-0000-000000000001:contract-invalid",
        datetime(2026, 10, 6, 11, 0, tzinfo=UTC),
    )
    effects = _effects(job_run, effect_types)

    assert _classify_recovery_evidence(
        frozenset(effect.effect_type for effect in effects)
    ) is not PaperRecoveryEvidence.COMPLETE

    repository = SqlAlchemyPaperReconciliationAuditRepository(_Connection())
    with pytest.raises(
        ValueError,
        match="PAPER_RECONCILIATION_AUDIT_EFFECT_SHAPE_INVALID",
    ):
        repository._expected(_completion(job_run), effects)
