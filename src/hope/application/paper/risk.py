from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffect, PaperEffectType, create_paper_effect
from hope.domain.risk.models import RiskAssessment


def paper_risk_payload_hash(assessment: RiskAssessment) -> str:
    """Hash the canonical material content of one PAPER risk assessment."""
    reason = assessment.reason_code.strip()
    if not reason or reason != assessment.reason_code:
        raise ValueError("PAPER_RISK_REASON_CODE_NOT_CANONICAL")

    canonical = "|".join(
        (
            str(assessment.signal_id),
            assessment.decision.value,
            reason,
            format(assessment.approved_quantity.normalize(), "f"),
        )
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class PaperRiskPersistence(Protocol):
    def persist(self, effect: PaperEffect, assessment: RiskAssessment) -> bool:
        ...


class PaperRiskWriter:
    """Persist one deterministic PAPER risk decision behind the effect boundary."""

    def __init__(self, repository: PaperRiskPersistence) -> None:
        self._repository = repository

    def record(self, context: PaperCycleContext, assessment: RiskAssessment) -> bool:
        if not isinstance(assessment, RiskAssessment):
            raise TypeError("PAPER_RISK_WRITER_REQUIRES_RISK_ASSESSMENT")

        effect = create_paper_effect(
            context.job_run,
            PaperEffectType.RISK,
            assessment.signal_id,
            paper_risk_payload_hash(assessment),
        )
        return self._repository.persist(effect, assessment)
