from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from hope.domain.execution.lifecycle import OrderLifecycle
from hope.domain.execution.models import ExecutionCancellation
from hope.domain.execution.simulator import Fill
from hope.domain.portfolio.ledger import PortfolioLedger, PortfolioState
from hope.domain.execution.session import ExecutionSession, ExecutionSessionError, ExecutionSessionState


class ExecutionReplayError(ValueError):
    pass


@dataclass(frozen=True)
class ReplayResult:
    state: ExecutionSessionState
    fills_applied: int
    filled_quantity: Decimal
    cancellation_applied: bool = False


def replay_order(
    order,
    fills: Sequence[Fill],
    *,
    cancellation: ExecutionCancellation | None = None,
    initial_cash: Decimal = Decimal("0"),
    initial_portfolio: PortfolioState | None = None,
) -> ReplayResult:
    """Replay an order's fills and optional terminal cancellation deterministically.

    Fills are applied in supplied event order. When a cancellation is supplied it
    must refer to the same order, follow every replayed fill, and report exactly
    the remaining quantity. Any lifecycle, identity, duplicate, temporal, or
    portfolio violation aborts the replay.
    """
    ledger = PortfolioLedger(initial_cash)
    if initial_portfolio is not None:
        # Initial state injection is intentionally unsupported until it has a
        # dedicated serialization contract; accepting it silently would make
        # replay provenance ambiguous.
        raise ExecutionReplayError("INITIAL_PORTFOLIO_REPLAY_NOT_SUPPORTED")

    session = ExecutionSession(OrderLifecycle(order), ledger)
    seen_ids: set = set()
    total_quantity = Decimal("0")
    signed_quantity = Decimal("0")
    last_fill_time = None

    for fill in fills:
        if fill.fill_id in seen_ids:
            raise ExecutionReplayError("DUPLICATE_FILL_EVENT")
        seen_ids.add(fill.fill_id)
        total_quantity += fill.quantity
        signed_quantity += fill.quantity if fill.side.value == "BUY" else -fill.quantity
        try:
            session.apply_fill(fill)
        except ExecutionSessionError as exc:
            raise ExecutionReplayError(str(exc)) from exc
        last_fill_time = fill.fill_time

    cancellation_applied = False
    if cancellation is not None:
        if cancellation.order_id != order.order_id:
            raise ExecutionReplayError("CANCELLATION_ORDER_MISMATCH")
        if cancellation.signal_id != order.signal_id:
            raise ExecutionReplayError("CANCELLATION_SIGNAL_MISMATCH")
        if cancellation.instrument_id != order.instrument_id:
            raise ExecutionReplayError("CANCELLATION_INSTRUMENT_MISMATCH")
        if cancellation.environment != order.environment:
            raise ExecutionReplayError("CANCELLATION_ENVIRONMENT_MISMATCH")
        if last_fill_time is not None and cancellation.cancellation_time < last_fill_time:
            raise ExecutionReplayError("CANCELLATION_PRECEDES_REPLAYED_FILL")
        if cancellation.cancelled_quantity != session.state.lifecycle.remaining_quantity:
            raise ExecutionReplayError("CANCELLATION_REMAINING_QUANTITY_MISMATCH")
        try:
            session.cancel()
        except ExecutionSessionError as exc:
            raise ExecutionReplayError(str(exc)) from exc
        cancellation_applied = True

    state = session.state
    if state.lifecycle.filled_quantity != total_quantity:
        raise ExecutionReplayError("LIFECYCLE_FILL_QUANTITY_MISMATCH")

    position = state.portfolio.positions.get(order.instrument_id)
    resulting_quantity = position.quantity if position else Decimal("0")
    if resulting_quantity != signed_quantity:
        raise ExecutionReplayError("PORTFOLIO_FILL_QUANTITY_MISMATCH")

    return ReplayResult(state, len(fills), total_quantity, cancellation_applied)
