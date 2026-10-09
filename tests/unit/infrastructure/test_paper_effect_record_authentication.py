"""The durable PAPER effect writer must not trust forged in-memory dataclasses."""

from uuid import uuid4

import pytest

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


class _NoDatabase:
    def execute(self, statement):
        pytest.fail("malformed effect must fail before database access")


@pytest.mark.parametrize(
    ("field", "replacement", "error"),
    [
        ("effect_id", uuid4(), "PAPER_EFFECT_IDENTITY_MISMATCH"),
        ("payload_hash", "not-a-hash", "PAPER_EFFECT_PAYLOAD_HASH_INVALID"),
        ("entity_id", "not-a-uuid", "PAPER_EFFECT_ENTITY_ID_INVALID"),
    ],
)
def test_record_rejects_forged_effect_without_database_access(field, replacement, error):
    entity_id = uuid4()
    from hope.application.paper.effects import _deterministic_effect_id

    effect = object.__new__(PaperEffect)
    for key, value in {
        "effect_id": _deterministic_effect_id(PaperEffectType.FILL, entity_id),
        "job_run_id": uuid4(),
        "effect_type": PaperEffectType.FILL,
        "entity_id": entity_id,
        "payload_hash": "a" * 64,
    }.items():
        object.__setattr__(effect, key, value)
    object.__setattr__(effect, field, replacement)
    repository = SqlAlchemyPaperEffectRepository(_NoDatabase())
    with pytest.raises(ValueError, match=error):
        repository.record(effect)


def test_record_rejects_non_effect_before_database_access():
    repository = SqlAlchemyPaperEffectRepository(_NoDatabase())
    with pytest.raises(ValueError, match="PAPER_EFFECT_RECORD_REQUIRES_EFFECT"):
        repository.record(object())


@pytest.mark.parametrize("corruption", ["wrong_owner", "duplicate_id", "duplicate_logical"])
def test_list_for_job_run_rejects_corrupt_effect_history(corruption):
    from hope.application.paper.effects import create_paper_effect
    from hope.application.jobs import create_scheduled_job_run
    from datetime import UTC, datetime

    run = create_scheduled_job_run(
        "paper:effect-history-integrity", datetime(2026, 10, 9, tzinfo=UTC),
    )
    effect = create_paper_effect(run, PaperEffectType.FILL, uuid4(), "a" * 64)
    row = {
        "effect_id": effect.effect_id, "job_run_id": effect.job_run_id,
        "effect_type": effect.effect_type.value, "entity_id": effect.entity_id,
        "payload_hash": effect.payload_hash,
    }
    if corruption == "wrong_owner":
        rows = [{**row, "job_run_id": uuid4()}]
        error = "PAPER_EFFECT_JOB_HISTORY_OWNER_MISMATCH"
    elif corruption == "duplicate_id":
        rows = [row, dict(row)]
        error = "PAPER_EFFECT_JOB_HISTORY_DUPLICATE"
    else:
        rows = [row, dict(row)]
        error = "PAPER_EFFECT_JOB_HISTORY_DUPLICATE"

    class _Rows:
        def mappings(self):
            return self

        def all(self):
            return rows

    class _Connection:
        def execute(self, statement):
            return _Rows()

    repository = SqlAlchemyPaperEffectRepository(_Connection())
    with pytest.raises(ValueError, match=error):
        repository.list_for_job_run(run.job_run_id)
