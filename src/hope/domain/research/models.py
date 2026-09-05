from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field

class ResearchDecision(StrEnum):
    SUPPORTED = "SUPPORTED"
    WEAK_EVIDENCE = "WEAK_EVIDENCE"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"

class ExperimentDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    experiment_id: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    universe_version: str = Field(min_length=1)
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
