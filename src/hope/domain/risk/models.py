from enum import StrEnum
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class RiskDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class RiskAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    signal_id: UUID
    decision: RiskDecision
    reason_code: str = Field(min_length=1)
    approved_quantity: Decimal = Field(ge=0)

    @property
    def approved(self) -> bool:
        return self.decision is RiskDecision.APPROVE
