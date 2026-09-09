from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from re import fullmatch
from uuid import NAMESPACE_URL, UUID, uuid5

from hope.application.jobs import ScheduledJobRun


class PaperEffectType(str, Enum):
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"
    PNL = "PNL"


def _deterministic_effect_id(effect_type: PaperEffectType, entity_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"hope:paper:effect:{effect_type.value}:{entity_id}")


@dataclass(frozen=True)
class PaperEffect:
    """One durable PAPER side effect attributable to a scheduled job run."""

    effect_id: UUID
    job_run_id: UUID
    effect_type: PaperEffectType
    entity_id: UUID
    payload_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.effect_id, UUID):
            raise ValueError("PAPER_EFFECT_ID_INVALID")
        if not isinstance(self.job_run_id, UUID):
            raise ValueError("PAPER_EFFECT_JOB_RUN_ID_INVALID")
        if not isinstance(self.entity_id, UUID):
            raise ValueError("PAPER_EFFECT_ENTITY_ID_INVALID")

        try:
            parsed_type = PaperEffectType(self.effect_type)
        except ValueError as exc:
            raise ValueError("PAPER_EFFECT_TYPE_INVALID") from exc
        object.__setattr__(self, "effect_type", parsed_type)

        if not isinstance(self.payload_hash, str) or fullmatch(r"[0-9a-f]{64}", self.payload_hash) is None:
            raise ValueError("PAPER_EFFECT_PAYLOAD_HASH_INVALID")

        expected_id = _deterministic_effect_id(parsed_type, self.entity_id)
        if self.effect_id != expected_id:
            raise ValueError("PAPER_EFFECT_IDENTITY_MISMATCH")


def create_paper_effect(
    job_run: ScheduledJobRun,
    effect_type: PaperEffectType,
    entity_id: UUID,
    payload_hash: str,
) -> PaperEffect:
    try:
        parsed_type = PaperEffectType(effect_type)
    except ValueError as exc:
        raise ValueError("PAPER_EFFECT_TYPE_INVALID") from exc
    return PaperEffect(
        effect_id=_deterministic_effect_id(parsed_type, entity_id),
        job_run_id=job_run.job_run_id,
        effect_type=parsed_type,
        entity_id=entity_id,
        payload_hash=payload_hash,
    )
