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
