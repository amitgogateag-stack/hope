from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hope.domain.market_intelligence.models import IntelligenceAction, MarketIntelligenceEvent


class IntelligenceDisposition(StrEnum):
    OBSERVE_ONLY = "OBSERVE_ONLY"
    ENTRY_ELIGIBILITY_REVIEW = "ENTRY_ELIGIBILITY_REVIEW"
    POSITION_RISK_REVIEW = "POSITION_RISK_REVIEW"
    MARKET_RISK_REVIEW = "MARKET_RISK_REVIEW"


_ACTION_DISPOSITIONS = {
    IntelligenceAction.NO_ACTION: IntelligenceDisposition.OBSERVE_ONLY,
    IntelligenceAction.OBSERVE: IntelligenceDisposition.OBSERVE_ONLY,
    IntelligenceAction.BLOCK_NEW_ENTRY: IntelligenceDisposition.ENTRY_ELIGIBILITY_REVIEW,
    IntelligenceAction.DATA_REVIEW_REQUIRED: IntelligenceDisposition.ENTRY_ELIGIBILITY_REVIEW,
    IntelligenceAction.REDUCE_RISK_CANDIDATE: IntelligenceDisposition.POSITION_RISK_REVIEW,
    IntelligenceAction.EXIT_CANDIDATE: IntelligenceDisposition.POSITION_RISK_REVIEW,
    IntelligenceAction.MARKET_RISK_HALT_CANDIDATE: IntelligenceDisposition.MARKET_RISK_REVIEW,
}


def _disposition_for_action(action: IntelligenceAction) -> IntelligenceDisposition:
    try:
        return _ACTION_DISPOSITIONS[action]
    except KeyError:
        raise ValueError("INTELLIGENCE_ACTION_UNMAPPED") from None


class IntelligenceAssessment(BaseModel):
    """Safe deterministic projection of intelligence into non-executable workflow state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    policy_version: str = Field(min_length=1)
    disposition: IntelligenceDisposition
    source_action: IntelligenceAction

    @model_validator(mode="after")
    def require_action_disposition_binding(self) -> IntelligenceAssessment:
        expected = _disposition_for_action(self.source_action)
        if self.disposition is not expected:
            raise ValueError("INTELLIGENCE_ASSESSMENT_DISPOSITION_MISMATCH")
        return self


def assess_intelligence_event(
    event: MarketIntelligenceEvent,
    *,
    policy_version: str = "hope.intelligence-policy.v1",
) -> IntelligenceAssessment:
    if not policy_version or policy_version != policy_version.strip():
        raise ValueError("INTELLIGENCE_POLICY_VERSION_NOT_CANONICAL")

    disposition = _disposition_for_action(event.recommended_action)

    return IntelligenceAssessment(
        event_id=event.event_id,
        policy_version=policy_version,
        disposition=disposition,
        source_action=event.recommended_action,
    )
