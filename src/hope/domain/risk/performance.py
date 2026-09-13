from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from hope.domain.portfolio.ledger import PortfolioLedger, PortfolioState


class PortfolioRiskPerformanceState(BaseModel):
    """Loss and drawdown facts derived from current equity and explicit anchors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_equity: Decimal
    session_start_equity: Decimal
    peak_equity: Decimal
    current_daily_loss: Decimal
    current_drawdown: Decimal

    @field_validator(
        "current_equity",
        "session_start_equity",
        "peak_equity",
        "current_daily_loss",
        "current_drawdown",
    )
    @classmethod
    def require_finite_values(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("PORTFOLIO_RISK_PERFORMANCE_VALUE_MUST_BE_FINITE")
        return value


def derive_portfolio_risk_performance_state(
    state: PortfolioState,
    marks: dict[UUID, Decimal],
    *,
    session_start_equity: Decimal,
    peak_equity: Decimal,
) -> PortfolioRiskPerformanceState:
    """Derive current equity, daily loss and drawdown from portfolio state.

    Session-start and peak equity remain explicit authoritative anchors because the
    current ledger stores present state, not valuation history. Peak equity must be
    at least the current and session-start equity if it truly represents the peak
    observed through the current decision point.
    """
    if not isinstance(state, PortfolioState):
        raise TypeError("PORTFOLIO_STATE_REQUIRED")
    if not session_start_equity.is_finite() or not peak_equity.is_finite():
        raise ValueError("PORTFOLIO_RISK_EQUITY_ANCHOR_MUST_BE_FINITE")

    PortfolioLedger.from_state(state, initial_cash=Decimal("0"))

    market_value = Decimal("0")
    for instrument_id, position in state.positions.items():
        if position.quantity == 0:
            continue
        if instrument_id not in marks:
            raise KeyError(f"MISSING_MARK:{instrument_id}")
        mark = marks[instrument_id]
        if not mark.is_finite():
            raise ValueError("MARK_PRICE_MUST_BE_FINITE")
        if mark <= 0:
            raise ValueError("MARK_PRICE_MUST_BE_POSITIVE")
        market_value += position.quantity * mark

    current_equity = state.cash + market_value
    if not current_equity.is_finite():
        raise ValueError("PORTFOLIO_RISK_CURRENT_EQUITY_MUST_BE_FINITE")
    if peak_equity < current_equity or peak_equity < session_start_equity:
        raise ValueError("PORTFOLIO_RISK_PEAK_EQUITY_INCONSISTENT")

    current_daily_loss = max(session_start_equity - current_equity, Decimal("0"))
    current_drawdown = peak_equity - current_equity

    return PortfolioRiskPerformanceState(
        current_equity=current_equity,
        session_start_equity=session_start_equity,
        peak_equity=peak_equity,
        current_daily_loss=current_daily_loss,
        current_drawdown=current_drawdown,
    )
