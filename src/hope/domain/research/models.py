from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResearchDecision(StrEnum):
    SUPPORTED = "SUPPORTED"
    WEAK_EVIDENCE = "WEAK_EVIDENCE"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"


class ExperimentDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str
    hypothesis: str
    strategy_version: str
    dataset_version: str
    universe_version: str
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "experiment_id",
        "strategy_version",
        "dataset_version",
        "universe_version",
    )
    @classmethod
    def require_canonical_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RESEARCH_IDENTITY_REQUIRED")
        if value != value.strip():
            raise ValueError("RESEARCH_IDENTITY_NOT_CANONICAL")
        return value

    @field_validator("hypothesis")
    @classmethod
    def require_canonical_hypothesis(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RESEARCH_HYPOTHESIS_REQUIRED")
        if value != value.strip():
            raise ValueError("RESEARCH_HYPOTHESIS_NOT_CANONICAL")
        return value
