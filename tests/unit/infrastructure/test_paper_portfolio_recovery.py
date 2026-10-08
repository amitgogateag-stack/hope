from datetime import UTC, datetime
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.effects import PaperEffectType, create_paper_effect
from hope.infrastructure.repositories.paper_portfolio import (
    SqlAlchemyPaperPortfolioRepository,
)


class _Effects:
    def __init__(self, *, recoverable=None, reusable=None, conflict=False):
        self.recoverable = recoverable
        self.reusable = reusable
        self.conflict = conflict
        self.calls = []

    def get_recoverable(self, effect_type, entity_id):
        self.calls.append(("recoverable", effect_type, entity_id))
        if self.conflict:
            raise ValueError("PAPER_EFFECT_OWNER_NOT_RECOVERABLE")
        return self.recoverable

    def get_reusable_for_job(self, effect_type, entity_id, job_run_id):
        self.calls.append(("reusable", effect_type, entity_id, job_run_id))
        if self.conflict:
            raise ValueError("PAPER_EFFECT_OWNER_NOT_RECOVERABLE")
        return self.reusable


def _repository(effects):
    repository = SqlAlchemyPaperPortfolioRepository(object())
    repository._effects = effects
    return repository


def test_public_portfolio_recovery_fails_closed_on_missing_execution_effect() -> None:
    entity_id = uuid4()
    effects = _Effects(recoverable=None)

    with pytest.raises(
        RuntimeError,
        match="PAPER_PORTFOLIO_EXECUTION_EFFECT_NOT_RECOVERABLE",
    ):
        _repository(effects)._require_recoverable_execution_effect(
            PaperEffectType.ORDER,
            entity_id,
            current_job_run_id=None,
            expected_payload_hash="a" * 64,
        )

    assert effects.calls == [("recoverable", PaperEffectType.ORDER, entity_id)]


def test_inflight_portfolio_recovery_fails_closed_on_conflicting_owner() -> None:
    entity_id, job_run_id = uuid4(), uuid4()
    effects = _Effects(conflict=True)

    with pytest.raises(
        RuntimeError,
        match="PAPER_PORTFOLIO_EXECUTION_OWNER_NOT_RECOVERABLE",
    ):
        _repository(effects)._require_recoverable_execution_effect(
            PaperEffectType.FILL,
            entity_id,
            current_job_run_id=job_run_id,
            expected_payload_hash="a" * 64,
        )

    assert effects.calls == [
        ("reusable", PaperEffectType.FILL, entity_id, job_run_id)
    ]


def test_portfolio_recovery_rejects_recoverable_effect_with_wrong_payload() -> None:
    run = create_scheduled_job_run(
        "paper-portfolio-payload",
        datetime(2026, 10, 8, tzinfo=UTC),
    )
    entity_id = uuid4()
    effect = create_paper_effect(
        run,
        PaperEffectType.SIGNAL,
        entity_id,
        "b" * 64,
    )
    effects = _Effects(recoverable=effect)

    with pytest.raises(
        RuntimeError,
        match="PAPER_PORTFOLIO_EXECUTION_PAYLOAD_MISMATCH",
    ):
        _repository(effects)._require_recoverable_execution_effect(
            PaperEffectType.SIGNAL,
            entity_id,
            current_job_run_id=None,
            expected_payload_hash="a" * 64,
        )
