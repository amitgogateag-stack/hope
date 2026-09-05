from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field
from hope.domain.execution.models import Environment

class ConfigurationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

class ProvenanceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dataset_version: str
    universe_version: str
    strategy_version: str
    parameter_snapshot_hash: str
    cost_model_version: str
    execution_model_version: str
    code_commit: str
    configuration_hash: str
    environment: Environment
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
