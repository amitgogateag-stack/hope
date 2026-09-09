from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence
from uuid import UUID

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
    initial_applied_fill_ids: Collection[UUID] = (),
) -> ReplayResult:
    """Replay an order's durable execution history into a safely resumable session when anchored."""
    if initial_portfolio is not None and initial_cash != Decimal("0"):
        raise ExecutionReplayError("INITIAL_CASH_AND_PORTFOLIO_MUTUALLY_EXCLUSIVE")
    if initial_applied_fill_ids and initial_portfolio is None:
        raise ExecutionReplayError("INITIAL_FILL_IDS_REQUIRE_PORTFOLIO_BASELINE")

    if initial_portfolio is not None:
        ledger = PortfolioLedger.from_state(
            initial_portfolio,
            applied_fill_ids=initial_applied_fill_ids,
        )
        baseline_position = initial_portfolio.positions.get(order.instrument_id)
        baseline_quantity = baseline_position.quantity if baseline_position else Decimal("0")
    else:
        ledger = PortfolioLedger(initial_cash)
        baseline_quantity = Decimal("0")

    if cancellation is not None and rejection is not None:
        raise ExecutionReplayError("TERMINAL_OUTCOMES_MUTUALLY_EXCLUSIVE")
    if order_time is not None and (order_time.tzinfo is None or order_time.utcoffset() is None):
        raise ExecutionReplayError("ORDER_TIME_MUST_BE_TIMEZONE_AWARE")

    session = ExecutionSession(OrderLifecycle(order), ledger, order_time=order_time)
    seen_ids: set = set()
    total_quantity = Decimal("0")
    signed_quantity = Decimal("0")
    last_fill_time = None

    for fill in fills:
        if fill.fill_id in seen_ids:
            raise ExecutionReplayError("DUPLICATE_FILL_EVENT")
        numeric_fields = (
            ("QUANTITY", fill.quantity),
            ("PRICE", fill.price),
            ("COMMISSION", fill.commission),
            ("SLIPPAGE", fill.slippage),
        )
        for field_name, value in numeric_fields:
            if not value.is_finite():
                raise ExecutionReplayError(f"FILL_{field_name}_MUST_BE_FINITE")
        if not fill.cost_model_version.strip():
            raise ExecutionReplayError("FILL_COST_MODEL_VERSION_REQUIRED")
        if fill.fill_time is None:
            raise ExecutionReplayError("FILL_TIME_REQUIRED")
        if fill.fill_time.tzinfo is None or fill.fill_time.utcoffset() is None:
            raise ExecutionReplayError("FILL_TIME_MUST_BE_TIMEZONE_AWARE")
        if last_fill_time is not None and fill.fill_time < last_fill_time:
            raise ExecutionReplayError("FILL_EVENTS_OUT_OF_TIME_ORDER")
        if order_time is not None and fill.fill_time < order_time:
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
    if resulting_quantity != baseline_quantity + signed_quantity:
        raise ExecutionReplayError("PORTFOLIO_FILL_QUANTITY_MISMATCH")

    resumable_session: ExecutionSession | None = None
    if state.lifecycle.status in {"OPEN", "PARTIALLY_FILLED"}:
        if last_fill_time is not None or order_time is not None:
            resumable_session = session

    return ReplayResult(
        state,
        len(fills),
        total_quantity,
        cancellation_applied,
        rejection_applied,
        resumable_session,
    )
