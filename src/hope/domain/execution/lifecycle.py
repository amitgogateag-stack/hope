from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from hope.domain.execution.models import Order
from hope.domain.execution.simulator import Fill


class OrderLifecycleError(ValueError):
    pass


@dataclass(frozen=True)
class OrderLifecycle:
    """Deterministic, append-only view of an order's fill lifecycle."""
    order: Order
    filled_quantity: Decimal = Decimal("0")
    cancelled: bool = False
    rejected: bool = False
    applied_fill_ids: frozenset[UUID] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.filled_quantity.is_finite():
            raise OrderLifecycleError("ORDER_LIFECYCLE_FILLED_QUANTITY_MUST_BE_FINITE")
        if self.filled_quantity < 0:
            raise OrderLifecycleError("ORDER_LIFECYCLE_FILLED_QUANTITY_MUST_BE_NON_NEGATIVE")
        if self.filled_quantity > self.order.quantity:
            raise OrderLifecycleError("ORDER_LIFECYCLE_FILLED_QUANTITY_EXCEEDS_ORDER_QUANTITY")
        if self.cancelled and self.rejected:
            raise OrderLifecycleError("ORDER_LIFECYCLE_TERMINAL_FLAGS_MUTUALLY_EXCLUSIVE")
        if self.rejected and self.filled_quantity != 0:
            raise OrderLifecycleError("ORDER_LIFECYCLE_REJECTED_MUST_BE_UNFILLED")
        if self.cancelled and self.filled_quantity >= self.order.quantity:
            raise OrderLifecycleError("ORDER_LIFECYCLE_CANCELLED_REQUIRES_REMAINING_QUANTITY")
        if self.filled_quantity == 0 and self.applied_fill_ids:
            raise OrderLifecycleError("ORDER_LIFECYCLE_FILL_HISTORY_WITHOUT_FILLED_QUANTITY")
        if self.filled_quantity > 0 and not self.applied_fill_ids:
            raise OrderLifecycleError("ORDER_LIFECYCLE_FILLED_QUANTITY_REQUIRES_FILL_HISTORY")

    @property
    def remaining_quantity(self) -> Decimal:
        return self.order.quantity - self.filled_quantity

    @property
    def status(self) -> str:
        if self.rejected:
            return "REJECTED"
        if self.cancelled:
            return "CANCELLED"
        if self.filled_quantity == 0:
            return "OPEN"
        if self.filled_quantity < self.order.quantity:
            return "PARTIALLY_FILLED"
        return "FILLED"

    def apply_fill(self, fill: Fill) -> "OrderLifecycle":
        if fill.fill_id in self.applied_fill_ids:
            raise OrderLifecycleError("DUPLICATE_FILL")
        if fill.order_id != self.order.order_id:
            raise OrderLifecycleError("FILL_ORDER_MISMATCH")
        if fill.signal_id != self.order.signal_id:
            raise OrderLifecycleError("FILL_SIGNAL_MISMATCH")
        if fill.instrument_id != self.order.instrument_id:
            raise OrderLifecycleError("FILL_INSTRUMENT_MISMATCH")
        if fill.side is not self.order.side:
            raise OrderLifecycleError("FILL_SIDE_MISMATCH")
        if self.cancelled or self.rejected:
            raise OrderLifecycleError("TERMINAL_ORDER_CANNOT_FILL")
        if fill.quantity <= 0:
            raise OrderLifecycleError("FILL_QUANTITY_MUST_BE_POSITIVE")
        if fill.quantity > self.remaining_quantity:
            raise OrderLifecycleError("FILL_EXCEEDS_REMAINING_ORDER_QUANTITY")
        return OrderLifecycle(
            self.order,
            self.filled_quantity + fill.quantity,
            self.cancelled,
            self.rejected,
            self.applied_fill_ids | {fill.fill_id},
        )

    def cancel(self) -> "OrderLifecycle":
        if self.rejected or self.cancelled or self.filled_quantity >= self.order.quantity:
            raise OrderLifecycleError("ORDER_NOT_CANCELLABLE")
        return OrderLifecycle(self.order, self.filled_quantity, True, False, self.applied_fill_ids)

    def reject(self) -> "OrderLifecycle":
        if self.cancelled or self.filled_quantity != 0 or self.rejected:
            raise OrderLifecycleError("ORDER_NOT_REJECTABLE")
        return OrderLifecycle(self.order, self.filled_quantity, False, True, self.applied_fill_ids)
