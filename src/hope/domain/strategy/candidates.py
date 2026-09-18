from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class StrategyMarket(StrEnum):
    INDIA = "INDIA"
    USA = "USA"


class StrategyCandidateState(StrEnum):
    RESEARCH = "RESEARCH"
    BACKUP_CANDIDATE = "BACKUP_CANDIDATE"
    OPERATIONAL_CANDIDATE = "OPERATIONAL_CANDIDATE"


class StrategyCandidateClassification(BaseModel):
    """Append-only classification of a versioned strategy for research operations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version_id: UUID
    markets: frozenset[StrategyMarket]
    state: StrategyCandidateState
    research_decision_id: str | None = None
    rationale: str

    @field_validator("markets")
    @classmethod
    def require_market_scope(
        cls, value: frozenset[StrategyMarket]
    ) -> frozenset[StrategyMarket]:
        if not value:
            raise ValueError("STRATEGY_CANDIDATE_MARKET_SCOPE_REQUIRED")
        return value

    @field_validator("research_decision_id")
    @classmethod
    def require_canonical_decision_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("STRATEGY_CANDIDATE_DECISION_ID_REQUIRED")
        if value != value.strip():
            raise ValueError("STRATEGY_CANDIDATE_DECISION_ID_NOT_CANONICAL")
        return value

    @field_validator("rationale")
    @classmethod
    def require_canonical_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("STRATEGY_CANDIDATE_RATIONALE_REQUIRED")
        if value != value.strip():
            raise ValueError("STRATEGY_CANDIDATE_RATIONALE_NOT_CANONICAL")
        return value

    @model_validator(mode="after")
    def require_evidence_for_nonresearch_state(self) -> "StrategyCandidateClassification":
        if (
            self.state is not StrategyCandidateState.RESEARCH
            and self.research_decision_id is None
        ):
            raise ValueError("STRATEGY_CANDIDATE_RESEARCH_DECISION_REQUIRED")
        return self
