from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from hope.domain.execution.lifecycle import OrderLifecycle
from hope.domain.execution.models import ExecutionCancellation, ExecutionRejection
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
    rejection_applied: bool = False
    session: ExecutionSession | None = None


def replay_order(
    order,
    fills: Sequence[Fill],
    *,
    cancellation: ExecutionCancellation | None = None,
    rejection: ExecutionRejection | None = None,
    order_time: datetime | None = None,
    initial_cash: Decimal = Decimal("0"),
    initial_portfolio: PortfolioState | None = None,
) -> ReplayResult:
    """Replay an order's durable execution history into a resumable session.

    Fills are applied in supplied event order. Their fill timestamps must be
    non-decreasing so replay cannot reconstruct a time-reversed execution history.
    A cancellation may follow zero or more fills and must report exactly the
    remaining quantity. An execution rejection is terminal only for an unfilled
    order. Terminal outcomes require enough timestamp evidence to prove they did
    not occur before order creation. Any lifecycle, identity, duplicate, temporal,
    or portfolio violation aborts replay. The returned session is the reconstructed
    execution session and may continue processing later fills after a restart when
    the replayed order remains open or partially filled.
    """
    ledger = PortfolioLedger(initial_cash)
    if initial_portfolio is not None:
        # Initial state injection is intentionally unsupported until it has a
        # dedicated serialization contract; accepting it silently would make
        # replay provenance ambiguous.
        raise ExecutionReplayError("INITIAL_PORTFOLIO_REPLAY_NOT_SUPPORTED")

    if cancellation is not None and rejection is not None:
        raise ExecutionReplayError("TERMINAL_OUTCOMES_MUTUALLY_EXCLUSIVE")
    if order_time is not None and (order_time.tzinfo is None or order_time.utcoffset() is None):
        raise ExecutionReplayError("ORDER_TIME_MUST_BE_TIMEZONE_AWARE")

    session = ExecutionSession(OrderLifecycle(order), ledger)
    seen_ids: set = set()
    total_quantity = Decimal("0")
    signed_quantity = Decimal("0")
    last_fill_time = None

    for fill in fills:
        if fill.fill_id in seen_ids:
            raise ExecutionReplayError("DUPLICATE_FILL_EVENT")
        if last_fill_time is not None and fill.fill_time < last_fill_time:
            raise ExecutionReplayError("FILL_EVENTS_OUT_OF_TIME_ORDER")
        if order_time is not None and fill.fill_time is not None and fill.fill_time < order_time:
            raise ExecutionReplayError("REPLAYED_FILL_PRECEDES_ORDER_TIME")
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
        if last_fill_time is None and order_time is None:
            raise ExecutionReplayError("CANCELLATION_ORDER_TIME_REQUIRED")
        if order_time is not None and cancellation.cancellation_time < order_time:
            raise ExecutionReplayError("CANCELLATION_PRECEDES_ORDER_TIME")
        if last_fill_time is not None and cancellation.cancellation_time < last_fill_time:
            raise ExecutionReplayError("CANCELLATION_PRECEDES_REPLAYED_FILL")
        if cancellation.cancelled_quantity != session.state.lifecycle.remaining_quantity:
            raise ExecutionReplayError("CANCELLATION_REMAINING_QUANTITY_MISMATCH")
        try:
            session.cancel()
        except ExecutionSessionError as exc:
            raise ExecutionReplayError(str(exc)) from exc
        cancellation_applied = True

    rejection_applied = False
    if rejection is not None:
        if rejection.order_id != order.order_id:
            raise ExecutionReplayError("REJECTION_ORDER_MISMATCH")
        if rejection.signal_id != order.signal_id:
            raise ExecutionReplayError("REJECTION_SIGNAL_MISMATCH")
        if rejection.instrument_id != order.instrument_id:
            raise ExecutionReplayError("REJECTION_INSTRUMENT_MISMATCH")
        if rejection.environment != order.environment:
            raise ExecutionReplayError("REJECTION_ENVIRONMENT_MISMATCH")
        if last_fill_time is not None:
            raise ExecutionReplayError("REJECTION_REQUIRES_UNFILLED_ORDER")
        if order_time is None:
            raise ExecutionReplayError("REJECTION_ORDER_TIME_REQUIRED")
        if rejection.rejection_time < order_time:
            raise ExecutionReplayError("REJECTION_PRECEDES_ORDER_TIME")
        try:
            session.reject()
        except ExecutionSessionError as exc:
            raise ExecutionReplayError(str(exc)) from exc
        rejection_applied = True

    state = session.state
    if state.lifecycle.filled_quantity != total_quantity:
        raise ExecutionReplayError("LIFECYCLE_FILL_QUANTITY_MISMATCH")

    position = state.portfolio.positions.get(order.instrument_id)
    resulting_quantity = position.quantity if position else Decimal("0")
    if resulting_quantity != signed_quantity:
        raise ExecutionReplayError("PORTFOLIO_FILL_QUANTITY_MISMATCH")

    return ReplayResult(
        state,
        len(fills),
        total_quantity,
        cancellation_applied,
        rejection_applied,
        session,
    )
