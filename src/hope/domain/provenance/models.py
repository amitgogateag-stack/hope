from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator

from hope.domain.execution.models import Environment


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("PROVENANCE_TIME_MUST_BE_TIMEZONE_AWARE")
    return value


class ConfigurationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    _require_aware_created_at = field_validator("created_at")(_require_aware)


class ProvenanceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dataset_version: str
    universe_version: str
    strategy_version: str
    parameter_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost_model_version: str
    execution_model_version: str
    code_commit: str
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment: Environment
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(
        "dataset_version",
        "universe_version",
        "strategy_version",
        "cost_model_version",
        "execution_model_version",
        "code_commit",
    )
    @classmethod
    def require_canonical_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("PROVENANCE_IDENTITY_REQUIRED")
        if value != value.strip():
            raise ValueError("PROVENANCE_IDENTITY_NOT_CANONICAL")
        return value

    _require_aware_created_at = field_validator("created_at")(_require_aware)
