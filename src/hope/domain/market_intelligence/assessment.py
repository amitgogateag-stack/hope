from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from hope.domain.market_intelligence.models import IntelligenceAction, MarketIntelligenceEvent


class IntelligenceDisposition(StrEnum):
    OBSERVE_ONLY = "OBSERVE_ONLY"
    ENTRY_ELIGIBILITY_REVIEW = "ENTRY_ELIGIBILITY_REVIEW"
    POSITION_RISK_REVIEW = "POSITION_RISK_REVIEW"
    MARKET_RISK_REVIEW = "MARKET_RISK_REVIEW"


class IntelligenceAssessment(BaseModel):
    """Safe deterministic projection of intelligence into non-executable workflow state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    policy_version: str = Field(min_length=1)
    disposition: IntelligenceDisposition
    source_action: IntelligenceAction


def assess_intelligence_event(
    event: MarketIntelligenceEvent,
    *,
    policy_version: str = "hope.intelligence-policy.v1",
) -> IntelligenceAssessment:
    if not policy_version or policy_version != policy_version.strip():
        raise ValueError("INTELLIGENCE_POLICY_VERSION_NOT_CANONICAL")

    if event.recommended_action in {
        IntelligenceAction.NO_ACTION,
        IntelligenceAction.OBSERVE,
    }:
        disposition = IntelligenceDisposition.OBSERVE_ONLY
    elif event.recommended_action in {
        IntelligenceAction.BLOCK_NEW_ENTRY,
        IntelligenceAction.DATA_REVIEW_REQUIRED,
    }:
        disposition = IntelligenceDisposition.ENTRY_ELIGIBILITY_REVIEW
    elif event.recommended_action in {
        IntelligenceAction.REDUCE_RISK_CANDIDATE,
        IntelligenceAction.EXIT_CANDIDATE,
    }:
        disposition = IntelligenceDisposition.POSITION_RISK_REVIEW
    elif event.recommended_action is IntelligenceAction.MARKET_RISK_HALT_CANDIDATE:
        disposition = IntelligenceDisposition.MARKET_RISK_REVIEW
    else:
        raise ValueError("INTELLIGENCE_ACTION_UNMAPPED")

    return IntelligenceAssessment(
        event_id=event.event_id,
        policy_version=policy_version,
        disposition=disposition,
        source_action=event.recommended_action,
    )
