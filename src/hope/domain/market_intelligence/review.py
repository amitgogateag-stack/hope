from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IntelligenceReviewOutcome(StrEnum):
    CLEARED = "CLEARED"
    BLOCK_CONFIRMED = "BLOCK_CONFIRMED"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"


class IntelligenceReviewResolution(BaseModel):
    """Immutable adjudication of one intelligence assessment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assessment_id: UUID
    policy_version: str = Field(min_length=1)
    outcome: IntelligenceReviewOutcome
    rationale: str = Field(min_length=1)

    @field_validator("policy_version", "rationale")
    @classmethod
    def require_canonical_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("INTELLIGENCE_REVIEW_TEXT_REQUIRED")
        if value != value.strip():
            raise ValueError("INTELLIGENCE_REVIEW_TEXT_NOT_CANONICAL")
        return value
