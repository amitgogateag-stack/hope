from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hope.domain.risk.models import RiskAssessment, RiskDecision


class PortfolioRiskReason(StrEnum):
    APPROVED = "PORTFOLIO_RISK_APPROVED"
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS_REACHED"
    MAX_DRAWDOWN = "MAX_DRAWDOWN_REACHED"
    MAX_POSITION_NOTIONAL = "MAX_POSITION_NOTIONAL_EXCEEDED"
    MAX_STRATEGY_EXPOSURE = "MAX_STRATEGY_EXPOSURE_EXCEEDED"
    CONCENTRATION_CONTEXT_REQUIRED = "CONCENTRATION_CONTEXT_REQUIRED"
    MAX_CONCENTRATION_EXPOSURE = "MAX_CONCENTRATION_EXPOSURE_EXCEEDED"
    MAX_GROSS_EXPOSURE = "MAX_GROSS_EXPOSURE_EXCEEDED"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS_EXCEEDED"


class PortfolioRiskLimits(BaseModel):
    """Explicit absolute portfolio limits for entry-risk evaluation.

    Threshold selection is intentionally external to this model. HOPE validates and
    applies the configured limits deterministically but does not invent leverage,
    loss, drawdown, strategy-allocation, or concentration percentages.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_position_notional: Decimal = Field(gt=0)
    max_gross_exposure: Decimal = Field(gt=0)
    max_open_positions: int = Field(ge=1)
    max_daily_loss: Decimal | None = Field(default=None, gt=0)
    max_drawdown: Decimal | None = Field(default=None, gt=0)
    max_strategy_exposure: Decimal | None = Field(default=None, gt=0)
    max_concentration_exposure: Decimal | None = Field(default=None, gt=0)

    @field_validator(
        "max_position_notional",
        "max_gross_exposure",
        "max_daily_loss",
        "max_drawdown",
        "max_strategy_exposure",
        "max_concentration_exposure",
    )
    @classmethod
    def require_finite_limits(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("PORTFOLIO_RISK_LIMIT_MUST_BE_FINITE")
        return value


class PortfolioConcentrationContext(BaseModel):
    """Externally resolved exposure context for one concentration group.

    The group may represent a sector, industry, theme, country, or another validated
    classification. Taxonomy ownership intentionally remains outside the risk engine.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    group: str = Field(min_length=1)
    current_exposure: Decimal = Field(ge=0)

    @field_validator("group")
    @classmethod
    def require_canonical_group(cls, value: str) -> str:
        if value.strip() != value or not value.strip():
            raise ValueError("PORTFOLIO_RISK_CONCENTRATION_GROUP_NOT_CANONICAL")
        return value

    @field_validator("current_exposure")
    @classmethod
    def require_finite_exposure(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("PORTFOLIO_RISK_CONCENTRATION_EXPOSURE_MUST_BE_FINITE")
        return value


class PortfolioEntryRiskRequest(BaseModel):
    """One proposed exposure-increasing entry or addition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: UUID
    instrument_id: UUID
    strategy_version: str = Field(min_length=1)
    proposed_quantity: Decimal = Field(gt=0)
    reference_price: Decimal = Field(gt=0)
    current_instrument_exposure: Decimal = Field(ge=0)
    current_strategy_exposure: Decimal = Field(ge=0)
    concentration: PortfolioConcentrationContext | None = None
    opens_new_position: bool

    @field_validator("strategy_version")
    @classmethod
    def require_canonical_strategy_version(cls, value: str) -> str:
        if value.strip() != value or not value.strip():
            raise ValueError("PORTFOLIO_RISK_STRATEGY_VERSION_NOT_CANONICAL")
        return value

    @field_validator(
        "proposed_quantity",
        "reference_price",
        "current_instrument_exposure",
        "current_strategy_exposure",
    )
    @classmethod
    def require_finite_request_numerics(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("PORTFOLIO_RISK_REQUEST_NUMERIC_MUST_BE_FINITE")
        return value

    @property
    def proposed_notional(self) -> Decimal:
        return self.proposed_quantity * self.reference_price

    @property
    def resulting_instrument_exposure(self) -> Decimal:
        return self.current_instrument_exposure + self.proposed_notional

    @property
    def resulting_strategy_exposure(self) -> Decimal:
        return self.current_strategy_exposure + self.proposed_notional

    @property
    def resulting_concentration_exposure(self) -> Decimal | None:
        if self.concentration is None:
            return None
        return self.concentration.current_exposure + self.proposed_notional


class PortfolioRiskSnapshot(BaseModel):
    """Pre-decision aggregate state used by the deterministic entry-risk engine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gross_exposure: Decimal = Field(ge=0)
    open_positions: int = Field(ge=0)
    current_daily_loss: Decimal = Field(ge=0)
    current_drawdown: Decimal = Field(ge=0)

    @field_validator("gross_exposure", "current_daily_loss", "current_drawdown")
    @classmethod
    def require_finite_snapshot_numerics(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("PORTFOLIO_RISK_SNAPSHOT_NUMERIC_MUST_BE_FINITE")
        return value


class PortfolioRiskEngine:
    """Deterministically apply configured absolute portfolio entry limits."""

    def __init__(self, limits: PortfolioRiskLimits) -> None:
        if not isinstance(limits, PortfolioRiskLimits):
            raise TypeError("PORTFOLIO_RISK_LIMITS_REQUIRED")
        self._limits = limits

    def assess_entry(
        self,
        request: PortfolioEntryRiskRequest,
        snapshot: PortfolioRiskSnapshot,
    ) -> RiskAssessment:
        if not isinstance(request, PortfolioEntryRiskRequest):
            raise TypeError("PORTFOLIO_ENTRY_RISK_REQUEST_REQUIRED")
        if not isinstance(snapshot, PortfolioRiskSnapshot):
            raise TypeError("PORTFOLIO_RISK_SNAPSHOT_REQUIRED")

        if (
            self._limits.max_daily_loss is not None
            and snapshot.current_daily_loss >= self._limits.max_daily_loss
        ):
            return self._reject(request.signal_id, PortfolioRiskReason.MAX_DAILY_LOSS)

        if (
            self._limits.max_drawdown is not None
            and snapshot.current_drawdown >= self._limits.max_drawdown
        ):
            return self._reject(request.signal_id, PortfolioRiskReason.MAX_DRAWDOWN)

        if request.resulting_instrument_exposure > self._limits.max_position_notional:
            return self._reject(request.signal_id, PortfolioRiskReason.MAX_POSITION_NOTIONAL)

        if (
            self._limits.max_strategy_exposure is not None
            and request.resulting_strategy_exposure > self._limits.max_strategy_exposure
        ):
            return self._reject(request.signal_id, PortfolioRiskReason.MAX_STRATEGY_EXPOSURE)

        if self._limits.max_concentration_exposure is not None:
            resulting_concentration = request.resulting_concentration_exposure
            if resulting_concentration is None:
                return self._reject(
                    request.signal_id,
                    PortfolioRiskReason.CONCENTRATION_CONTEXT_REQUIRED,
                )
            if resulting_concentration > self._limits.max_concentration_exposure:
                return self._reject(
                    request.signal_id,
                    PortfolioRiskReason.MAX_CONCENTRATION_EXPOSURE,
                )

        if snapshot.gross_exposure + request.proposed_notional > self._limits.max_gross_exposure:
            return self._reject(request.signal_id, PortfolioRiskReason.MAX_GROSS_EXPOSURE)

        if request.opens_new_position and snapshot.open_positions >= self._limits.max_open_positions:
            return self._reject(request.signal_id, PortfolioRiskReason.MAX_OPEN_POSITIONS)

        return RiskAssessment(
            signal_id=request.signal_id,
            decision=RiskDecision.APPROVE,
            reason_code=PortfolioRiskReason.APPROVED,
            approved_quantity=request.proposed_quantity,
        )

    @staticmethod
    def _reject(signal_id: UUID, reason: PortfolioRiskReason) -> RiskAssessment:
        return RiskAssessment(
            signal_id=signal_id,
            decision=RiskDecision.REJECT,
            reason_code=reason,
            approved_quantity=Decimal("0"),
        )
