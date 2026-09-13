from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hope.domain.portfolio.ledger import PortfolioLedger, PortfolioState


class PortfolioExposureState(BaseModel):
    """Exposure facts derived from authoritative portfolio positions and marks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gross_exposure: Decimal = Field(ge=0)
    open_positions: int = Field(ge=0)
    current_instrument_exposure: Decimal = Field(ge=0)
    opens_new_position: bool

    @field_validator("gross_exposure", "current_instrument_exposure")
    @classmethod
    def require_finite_exposures(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("PORTFOLIO_EXPOSURE_MUST_BE_FINITE")
        return value


def derive_portfolio_exposure_state(
    state: PortfolioState,
    marks: dict[UUID, Decimal],
    instrument_id: UUID,
) -> PortfolioExposureState:
    """Derive position-count and notional exposure from authoritative state.

    Gross exposure is the sum of absolute marked notionals for all open positions.
    Closed historical positions do not count as open and do not require a mark.
    """
    if not isinstance(state, PortfolioState):
        raise TypeError("PORTFOLIO_STATE_REQUIRED")

    # Reuse ledger restore validation for state-key and numeric integrity without
    # imposing any assumption about the original funding amount.
    PortfolioLedger.from_state(state, initial_cash=Decimal("0"))

    gross_exposure = Decimal("0")
    open_positions = 0
    current_instrument_exposure = Decimal("0")
    target_is_open = False

    for open_instrument_id, position in state.positions.items():
        if position.quantity == 0:
            continue
        if open_instrument_id not in marks:
            raise KeyError(f"MISSING_MARK:{open_instrument_id}")
        mark = marks[open_instrument_id]
        if not mark.is_finite():
            raise ValueError("MARK_PRICE_MUST_BE_FINITE")
        if mark <= 0:
            raise ValueError("MARK_PRICE_MUST_BE_POSITIVE")

        notional = abs(position.quantity) * mark
        gross_exposure += notional
        open_positions += 1

        if open_instrument_id == instrument_id:
            current_instrument_exposure = notional
            target_is_open = True

    return PortfolioExposureState(
        gross_exposure=gross_exposure,
        open_positions=open_positions,
        current_instrument_exposure=current_instrument_exposure,
        opens_new_position=not target_is_open,
    )
