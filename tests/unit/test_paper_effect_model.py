from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperEffect, PaperEffectType, create_paper_effect


UTC = timezone.utc
PAYLOAD = "a" * 64


def make_run(offset_minutes: int = 0):
    return create_scheduled_job_run(
        "paper-effect-model",
        datetime(2026, 9, 9, 22, 0, tzinfo=UTC) + timedelta(minutes=offset_minutes),
    )


def test_paper_effect_identity_is_stable_across_job_runs_for_same_logical_entity() -> None:
    entity_id = uuid4()
    first = create_paper_effect(make_run(), PaperEffectType.SIGNAL, entity_id, PAYLOAD)
    second = create_paper_effect(make_run(1), PaperEffectType.SIGNAL, entity_id, PAYLOAD)

    assert first.effect_id == second.effect_id
    assert first.job_run_id != second.job_run_id


def test_paper_effect_type_is_part_of_identity() -> None:
    entity_id = uuid4()
    job_run = make_run()

    signal = create_paper_effect(job_run, PaperEffectType.SIGNAL, entity_id, PAYLOAD)
    order = create_paper_effect(job_run, PaperEffectType.ORDER, entity_id, PAYLOAD)

    assert signal.effect_id != order.effect_id


def test_paper_effect_rejects_invalid_payload_hash() -> None:
    with pytest.raises(ValueError, match="PAPER_EFFECT_PAYLOAD_HASH_INVALID"):
        create_paper_effect(make_run(), PaperEffectType.FILL, uuid4(), "not-a-hash")


def test_paper_effect_rejects_arbitrary_restored_effect_id() -> None:
    job_run = make_run()
    entity_id = uuid4()

    with pytest.raises(ValueError, match="PAPER_EFFECT_IDENTITY_MISMATCH"):
        PaperEffect(
            effect_id=uuid4(),
            job_run_id=job_run.job_run_id,
            effect_type=PaperEffectType.PNL,
            entity_id=entity_id,
            payload_hash=PAYLOAD,
        )


def test_paper_effect_rejects_invalid_type_on_restoration() -> None:
    job_run = make_run()
    entity_id = uuid4()

    with pytest.raises(ValueError, match="PAPER_EFFECT_TYPE_INVALID"):
        PaperEffect(
            effect_id=uuid4(),
            job_run_id=job_run.job_run_id,
            effect_type="UNKNOWN",
            entity_id=entity_id,
            payload_hash=PAYLOAD,
        )
