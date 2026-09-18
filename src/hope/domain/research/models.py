from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class ExperimentVariantDefinition(BaseModel):
    """Immutable, predeclared control/variant relationship for research comparison."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    control_experiment_id: str
    variant_experiment_id: str
    variant_label: str

    @field_validator("control_experiment_id", "variant_experiment_id")
    @classmethod
    def require_canonical_experiment_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RESEARCH_VARIANT_EXPERIMENT_ID_REQUIRED")
        if value != value.strip():
            raise ValueError("RESEARCH_VARIANT_EXPERIMENT_ID_NOT_CANONICAL")
        return value

    @field_validator("variant_label")
    @classmethod
    def require_canonical_variant_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RESEARCH_VARIANT_LABEL_REQUIRED")
        if value != value.strip():
            raise ValueError("RESEARCH_VARIANT_LABEL_NOT_CANONICAL")
        return value

    @model_validator(mode="after")
    def require_distinct_control_and_variant(self) -> "ExperimentVariantDefinition":
        if self.control_experiment_id == self.variant_experiment_id:
            raise ValueError("RESEARCH_VARIANT_CONTROL_MUST_DIFFER")
        return self


class ResearchDecisionDefinition(BaseModel):
    """Immutable decision over one evidence-backed control/variant run pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    variant_experiment_id: str
    control_run_id: UUID
    variant_run_id: UUID
    decision: ResearchDecision
    rationale: str

    @field_validator("decision_id", "variant_experiment_id")
    @classmethod
    def require_canonical_decision_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RESEARCH_DECISION_IDENTITY_REQUIRED")
        if value != value.strip():
            raise ValueError("RESEARCH_DECISION_IDENTITY_NOT_CANONICAL")
        return value

    @field_validator("rationale")
    @classmethod
    def require_canonical_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RESEARCH_DECISION_RATIONALE_REQUIRED")
        if value != value.strip():
            raise ValueError("RESEARCH_DECISION_RATIONALE_NOT_CANONICAL")
        return value
