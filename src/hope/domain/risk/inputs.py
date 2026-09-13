from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from hope.domain.portfolio.ledger import PortfolioState
from hope.domain.risk.exposure import derive_portfolio_exposure_state
from hope.domain.risk.performance import derive_portfolio_risk_performance_state
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
    session_start_equity: Decimal,
    peak_equity: Decimal,
    concentration: PortfolioConcentrationContext | None = None,
) -> PortfolioEntryRiskInputs:
    """Build risk inputs while deriving portfolio exposure and performance state.

    Portfolio-wide and target-position exposure facts are never caller supplied.
    Daily loss and drawdown are derived from the same marked portfolio state plus
    explicit session-start and peak-equity anchors. Strategy exposure remains an
    explicit required input because the current portfolio ledger cannot recover
    strategy lineage. Concentration stays optional because the risk engine fails
    closed when a configured concentration limit requires missing classification.
    """
    exposure = derive_portfolio_exposure_state(
        portfolio_state,
        marks,
        instrument_id,
    )
    performance = derive_portfolio_risk_performance_state(
        portfolio_state,
        marks,
        session_start_equity=session_start_equity,
        peak_equity=peak_equity,
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
        current_daily_loss=performance.current_daily_loss,
        current_drawdown=performance.current_drawdown,
    )
    return PortfolioEntryRiskInputs(request=request, snapshot=snapshot)
