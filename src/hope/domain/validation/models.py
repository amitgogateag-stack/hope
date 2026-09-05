from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field

class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"

class InvariantResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    invariant_id: str = Field(min_length=1)
    status: ValidationStatus
    message: str
