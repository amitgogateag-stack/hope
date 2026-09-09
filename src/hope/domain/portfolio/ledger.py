from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict
from uuid import UUID

from hope.domain.execution.models import OrderSide
from hope.domain.execution.simulator import Fill


@dataclass(frozen=True)
class PositionState:
    instrument_id: UUID
    quantity: Decimal
    average_price: Decimal
    realized_pnl: Decimal
    total_commission: Decimal


@dataclass(frozen=True)
class PortfolioState:
    cash: Decimal
    positions: Dict[UUID, PositionState]

    @property
    def realized_pnl(self) -> Decimal:
        return sum((p.realized_pnl for p in self.positions.values()), Decimal("0"))

    @property
    def commissions(self) -> Decimal:
        return sum((p.total_commission for p in self.positions.values()), Decimal("0"))


class PortfolioLedger:
    """Deterministic average-cost portfolio ledger.

    The ledger is intentionally immutable at the public state boundary: each fill
    produces a new PortfolioState. Quantity is signed (long positive, short
    negative). Cash is reduced by buy consideration and increased by sell
    consideration; commissions are always a cash outflow.
    """

    def __init__(self, initial_cash: Decimal = Decimal("0")) -> None:
        if initial_cash < 0:
            raise ValueError("INITIAL_CASH_MUST_BE_NON_NEGATIVE")
        self._initial_cash = initial_cash
        self._state = PortfolioState(initial_cash, {})
        self._applied_fill_ids: set[UUID] = set()

    @classmethod
    def from_state(cls, state: PortfolioState) -> "PortfolioLedger":
        """Restore a ledger from an already-materialized domain state baseline."""
        for instrument_id, position in state.positions.items():
            if position.instrument_id != instrument_id:
                raise ValueError("PORTFOLIO_POSITION_KEY_MISMATCH")
            if position.quantity != 0 and position.average_price <= 0:
                raise ValueError("PORTFOLIO_OPEN_POSITION_AVERAGE_PRICE_MUST_BE_POSITIVE")
            if position.quantity == 0 and position.average_price != 0:
                raise ValueError("PORTFOLIO_FLAT_POSITION_AVERAGE_PRICE_MUST_BE_ZERO")
        ledger = cls(Decimal("0"))
        ledger._initial_cash = state.cash
        ledger._state = PortfolioState(state.cash, dict(state.positions))
        return ledger

    @property
    def initial_cash(self) -> Decimal:
        return self._initial_cash

    @property
    def state(self) -> PortfolioState:
        return self._state

    def apply_fill(self, fill: Fill) -> PortfolioState:
        if fill.fill_id in self._applied_fill_ids:
            raise ValueError("DUPLICATE_FILL")
        if fill.quantity <= 0:
            raise ValueError("FILL_QUANTITY_MUST_BE_POSITIVE")
        if fill.price <= 0:
            raise ValueError("FILL_PRICE_MUST_BE_POSITIVE")
        if fill.commission < 0 or fill.slippage < 0:
            raise ValueError("FILL_COSTS_MUST_BE_NON_NEGATIVE")

        current = self._state.positions.get(fill.instrument_id)
        current_qty = current.quantity if current else Decimal("0")
        signed_qty = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        new_qty = current_qty + signed_qty

        realized = current.realized_pnl if current else Decimal("0")
        avg_price = current.average_price if current else Decimal("0")

        # Same-direction increase, or opening a new position.
        if current_qty == 0 or (current_qty > 0 and signed_qty > 0) or (current_qty < 0 and signed_qty < 0):
            total_abs = abs(current_qty) + abs(signed_qty)
            avg_price = ((abs(current_qty) * avg_price) + (abs(signed_qty) * fill.price)) / total_abs
        else:
            # Closing/reducing an existing position. Average-cost accounting
            # realizes P&L only on the quantity that offsets the old position.
            closing_qty = min(abs(current_qty), abs(signed_qty))
            if current_qty > 0:  # selling a long
                realized += closing_qty * (fill.price - avg_price)
            else:  # buying back a short
                realized += closing_qty * (avg_price - fill.price)

            # If the fill reverses the position, the excess opens at fill price.
            if new_qty != 0 and abs(signed_qty) > abs(current_qty):
                avg_price = fill.price
            elif new_qty == 0:
                avg_price = Decimal("0")

        consideration = fill.price * fill.quantity
        cash_delta = -consideration if fill.side is OrderSide.BUY else consideration
        cash_delta -= fill.commission

        positions = dict(self._state.positions)
        positions[fill.instrument_id] = PositionState(
            instrument_id=fill.instrument_id,
            quantity=new_qty,
            average_price=avg_price,
            realized_pnl=realized,
            total_commission=(current.total_commission if current else Decimal("0")) + fill.commission,
        )
        if new_qty == 0:
            # Keep the closed position's realized history for auditability.
            positions[fill.instrument_id] = PositionState(
                instrument_id=fill.instrument_id,
                quantity=Decimal("0"),
                average_price=Decimal("0"),
                realized_pnl=realized,
                total_commission=(current.total_commission if current else Decimal("0")) + fill.commission,
            )

        self._state = PortfolioState(self._state.cash + cash_delta, positions)
        self._applied_fill_ids.add(fill.fill_id)
        return self._state

    def unrealized_pnl(self, marks: dict[UUID, Decimal]) -> Decimal:
        total = Decimal("0")
        for instrument_id, position in self._state.positions.items():
            if position.quantity == 0:
                continue
            if instrument_id not in marks:
                raise KeyError(f"MISSING_MARK:{instrument_id}")
            mark = marks[instrument_id]
            if mark <= 0:
                raise ValueError("MARK_PRICE_MUST_BE_POSITIVE")
            if position.quantity > 0:
                total += position.quantity * (mark - position.average_price)
            else:
                total += abs(position.quantity) * (position.average_price - mark)
        return total
