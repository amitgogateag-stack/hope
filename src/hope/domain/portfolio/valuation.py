from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from hope.domain.portfolio.ledger import PortfolioLedger, PortfolioState


@dataclass(frozen=True)
class PortfolioValuation:
    """Point-in-time portfolio valuation derived from a ledger state and marks."""

    as_of: datetime
    cash: Decimal
    market_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    commissions: Decimal
    equity: Decimal
    total_pnl: Decimal


def value_portfolio(
    ledger: PortfolioLedger,
    marks: dict[UUID, Decimal],
    as_of: datetime,
) -> PortfolioValuation:
    """Value every open position at the supplied point-in-time marks.

    Market value is signed: long positions contribute positive value and short
    positions contribute negative value. Total P&L is equity minus initial cash,
    which reconciles realized + unrealized P&L less commissions.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("VALUATION_TIME_MUST_BE_TIMEZONE_AWARE")

    state: PortfolioState = ledger.state
    market_value = Decimal("0")
    unrealized = Decimal("0")

    for instrument_id, position in state.positions.items():
        if position.quantity == 0:
            continue
        if instrument_id not in marks:
            raise KeyError(f"MISSING_MARK:{instrument_id}")
        mark = marks[instrument_id]
        if mark <= 0:
            raise ValueError("MARK_PRICE_MUST_BE_POSITIVE")
        market_value += position.quantity * mark

        if position.quantity > 0:
            unrealized += position.quantity * (mark - position.average_price)
        else:
            unrealized += abs(position.quantity) * (position.average_price - mark)

    equity = state.cash + market_value
    total_pnl = equity - ledger.initial_cash

    return PortfolioValuation(
        as_of=as_of,
        cash=state.cash,
        market_value=market_value,
        realized_pnl=state.realized_pnl,
        unrealized_pnl=unrealized,
        commissions=state.commissions,
        equity=equity,
        total_pnl=total_pnl,
    )
