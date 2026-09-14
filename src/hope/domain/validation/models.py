from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, field_validator

class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"

class InvariantResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    invariant_id: str = Field(min_length=1)
    status: ValidationStatus
    message: str

    @field_validator("invariant_id")
    @classmethod
    def require_canonical_invariant_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("INVARIANT_ID_REQUIRED")
        if value != value.strip():
            raise ValueError("INVARIANT_ID_NOT_CANONICAL")
        return value
