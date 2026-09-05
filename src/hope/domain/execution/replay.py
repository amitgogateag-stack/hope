from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from hope.domain.execution.lifecycle import OrderLifecycle
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


def replay_order(
    order,
    fills: Sequence[Fill],
    *,
    initial_cash: Decimal = Decimal("0"),
    initial_portfolio: PortfolioState | None = None,
) -> ReplayResult:
    """Replay an order's fills and prove lifecycle/portfolio quantity agreement.

    The replay is deterministic: fills are applied in the supplied event order.
    Any lifecycle, identity, duplicate, or portfolio violation aborts the replay.
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

    state = session.state
    if state.lifecycle.filled_quantity != total_quantity:
        raise ExecutionReplayError("LIFECYCLE_FILL_QUANTITY_MISMATCH")

    position = state.portfolio.positions.get(order.instrument_id)
    resulting_quantity = position.quantity if position else Decimal("0")
    if resulting_quantity != signed_quantity:
        raise ExecutionReplayError("PORTFOLIO_FILL_QUANTITY_MISMATCH")

    return ReplayResult(state, len(fills), total_quantity)
