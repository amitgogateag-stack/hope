from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from hope.domain.portfolio.ledger import PortfolioState


@dataclass(frozen=True)
class PortfolioExposureState:
    """Authoritative exposure facts derived from one portfolio state and PIT marks."""

    gross_exposure: Decimal
    current_instrument_exposure: Decimal
    open_positions: int
    opens_new_position: bool


def derive_portfolio_exposure_state(
    state: PortfolioState,
    marks: dict[UUID, Decimal],
    target_instrument_id: UUID,
) -> PortfolioExposureState:
    """Derive mark-to-market exposure without trusting caller-supplied aggregates.

    Gross exposure is the sum of absolute marked notionals for every open position.
    Long and short positions therefore consume risk capacity symmetrically. Flat
    positions require no mark and are not counted as open positions.
    """
    if not isinstance(state, PortfolioState):
        raise TypeError("PORTFOLIO_STATE_REQUIRED")
    if not isinstance(target_instrument_id, UUID):
        raise TypeError("TARGET_INSTRUMENT_ID_REQUIRED")

    gross_exposure = Decimal("0")
    current_instrument_exposure = Decimal("0")
    open_positions = 0

    for instrument_id, position in state.positions.items():
        if position.instrument_id != instrument_id:
            raise ValueError("PORTFOLIO_POSITION_KEY_MISMATCH")
        if not position.quantity.is_finite():
            raise ValueError("PORTFOLIO_POSITION_QUANTITY_MUST_BE_FINITE")
        if position.quantity == 0:
            continue
        if instrument_id not in marks:
            raise KeyError(f"MISSING_MARK:{instrument_id}")

        mark = marks[instrument_id]
        if not mark.is_finite():
            raise ValueError("MARK_PRICE_MUST_BE_FINITE")
        if mark <= 0:
            raise ValueError("MARK_PRICE_MUST_BE_POSITIVE")

        exposure = abs(position.quantity) * mark
        gross_exposure += exposure
        open_positions += 1
        if instrument_id == target_instrument_id:
            current_instrument_exposure = exposure

    target_position = state.positions.get(target_instrument_id)
    opens_new_position = target_position is None or target_position.quantity == 0

    return PortfolioExposureState(
        gross_exposure=gross_exposure,
        current_instrument_exposure=current_instrument_exposure,
        open_positions=open_positions,
        opens_new_position=opens_new_position,
    )


def derive_portfolio_exposure(
    state: PortfolioState,
    marks: dict[UUID, Decimal],
    target_instrument_id: UUID,
) -> PortfolioExposureState:
    """Backward-compatible alias for the canonical exposure-state derivation."""
    return derive_portfolio_exposure_state(state, marks, target_instrument_id)
