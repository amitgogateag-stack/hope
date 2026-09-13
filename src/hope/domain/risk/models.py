from enum import StrEnum
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RiskDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class RiskAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    signal_id: UUID
    decision: RiskDecision
    reason_code: str = Field(min_length=1)
    approved_quantity: Decimal = Field(ge=0)

    @field_validator("approved_quantity")
    @classmethod
    def require_finite_approved_quantity(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("RISK_APPROVED_QUANTITY_MUST_BE_FINITE")
        return value

    @model_validator(mode="after")
    def require_rejection_has_zero_quantity(self) -> "RiskAssessment":
        if self.decision is RiskDecision.REJECT and self.approved_quantity != 0:
            raise ValueError("RISK_REJECTION_REQUIRES_ZERO_QUANTITY")
        return self

    @property
    def approved(self) -> bool:
        return self.decision is RiskDecision.APPROVE
