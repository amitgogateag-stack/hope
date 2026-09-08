from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CounterfactualExitAssumption(StrEnum):
    MARK_TO_HORIZON = "MARK_TO_HORIZON"


class CounterfactualAcceptancePolicy(BaseModel):
    """Immutable assumptions for an isolated refused-signal acceptance replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    holding_period: timedelta
    execution_assumption: Literal["SAME_AS_PRIMARY"] = "SAME_AS_PRIMARY"
    exit_assumption: CounterfactualExitAssumption = CounterfactualExitAssumption.MARK_TO_HORIZON

    @field_validator("policy_id", "version")
    @classmethod
    def require_nonblank_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("COUNTERFACTUAL_POLICY_IDENTIFIER_REQUIRED")
        return value

    @field_validator("holding_period")
    @classmethod
    def require_positive_holding_period(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("COUNTERFACTUAL_HOLDING_PERIOD_MUST_BE_POSITIVE")
        return value
