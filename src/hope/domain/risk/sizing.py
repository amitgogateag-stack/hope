from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PositionSizingRequest(BaseModel):
    """Explicit cash-risk sizing inputs for one proposed position.

    HOPE does not invent a risk percentage, stop distance, or quantity granularity.
    The caller must supply an absolute cash-risk budget, entry price, protective stop,
    and quantity step that is valid for the instrument/execution venue.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_budget_cash: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    quantity_step: Decimal = Field(gt=0)

    @field_validator(
        "risk_budget_cash",
        "entry_price",
        "stop_price",
        "quantity_step",
    )
    @classmethod
    def require_finite_values(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("POSITION_SIZING_VALUE_MUST_BE_FINITE")
        return value

    @model_validator(mode="after")
    def require_nonzero_stop_distance(self) -> "PositionSizingRequest":
        if self.entry_price == self.stop_price:
            raise ValueError("POSITION_SIZING_STOP_DISTANCE_MUST_BE_POSITIVE")
        return self

    @property
    def risk_per_unit(self) -> Decimal:
        return abs(self.entry_price - self.stop_price)


class PositionSizingResult(BaseModel):
    """Deterministic quantity sized without exceeding the explicit cash-risk budget."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quantity: Decimal = Field(ge=0)
    risk_per_unit: Decimal = Field(gt=0)
    planned_cash_risk: Decimal = Field(ge=0)
    unused_cash_risk_budget: Decimal = Field(ge=0)

    @field_validator(
        "quantity",
        "risk_per_unit",
        "planned_cash_risk",
        "unused_cash_risk_budget",
    )
    @classmethod
    def require_finite_values(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("POSITION_SIZING_RESULT_MUST_BE_FINITE")
        return value


def size_position(request: PositionSizingRequest) -> PositionSizingResult:
    """Floor quantity to the configured step while staying within risk budget."""
    if not isinstance(request, PositionSizingRequest):
        raise TypeError("POSITION_SIZING_REQUEST_REQUIRED")

    risk_per_unit = request.risk_per_unit
    raw_steps = request.risk_budget_cash / (risk_per_unit * request.quantity_step)
    whole_steps = raw_steps.to_integral_value(rounding=ROUND_FLOOR)
    quantity = whole_steps * request.quantity_step
    planned_cash_risk = quantity * risk_per_unit
    unused_cash_risk_budget = request.risk_budget_cash - planned_cash_risk

    if planned_cash_risk > request.risk_budget_cash:
        raise RuntimeError("POSITION_SIZING_BUDGET_EXCEEDED")

    return PositionSizingResult(
        quantity=quantity,
        risk_per_unit=risk_per_unit,
        planned_cash_risk=planned_cash_risk,
        unused_cash_risk_budget=unused_cash_risk_budget,
    )
