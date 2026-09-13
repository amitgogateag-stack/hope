from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from hope.domain.portfolio.ledger import PortfolioState
from hope.domain.risk.exposure import derive_portfolio_exposure_state
from hope.domain.risk.portfolio import (
    PortfolioConcentrationContext,
    PortfolioEntryRiskRequest,
    PortfolioRiskSnapshot,
)


@dataclass(frozen=True)
class PortfolioEntryRiskInputs:
    """Authoritative entry-risk request plus aggregate snapshot."""

    request: PortfolioEntryRiskRequest
    snapshot: PortfolioRiskSnapshot


def build_portfolio_entry_risk_inputs(
    *,
    signal_id: UUID,
    instrument_id: UUID,
    strategy_version: str,
    proposed_quantity: Decimal,
    reference_price: Decimal,
    portfolio_state: PortfolioState,
    marks: dict[UUID, Decimal],
    current_strategy_exposure: Decimal,
    concentration: PortfolioConcentrationContext | None = None,
    current_daily_loss: Decimal,
    current_drawdown: Decimal,
) -> PortfolioEntryRiskInputs:
    """Build risk inputs while deriving portfolio exposure from ledger state.

    Portfolio-wide and target-position exposure facts are never caller supplied.
    Strategy exposure and loss/drawdown remain explicit required inputs because the
    current portfolio ledger cannot authoritatively derive that lineage/history.
    Concentration stays optional because the risk engine fails closed when a
    configured concentration limit requires missing classification context.
    """
    exposure = derive_portfolio_exposure_state(
        portfolio_state,
        marks,
        instrument_id,
    )

    request = PortfolioEntryRiskRequest(
        signal_id=signal_id,
        instrument_id=instrument_id,
        strategy_version=strategy_version,
        proposed_quantity=proposed_quantity,
        reference_price=reference_price,
        current_instrument_exposure=exposure.current_instrument_exposure,
        current_strategy_exposure=current_strategy_exposure,
        concentration=concentration,
        opens_new_position=exposure.opens_new_position,
    )
    snapshot = PortfolioRiskSnapshot(
        gross_exposure=exposure.gross_exposure,
        open_positions=exposure.open_positions,
        current_daily_loss=current_daily_loss,
        current_drawdown=current_drawdown,
    )
    return PortfolioEntryRiskInputs(request=request, snapshot=snapshot)
