from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from hope.domain.execution.lifecycle import OrderLifecycle, OrderLifecycleError
from hope.domain.execution.simulator import Fill
from hope.domain.portfolio.ledger import PortfolioLedger, PortfolioState


class ExecutionSessionError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionSessionState:
    lifecycle: OrderLifecycle
    portfolio: PortfolioState
    fill_ids: frozenset[UUID]


class ExecutionSession:
    """Coordinates one order lifecycle with the portfolio ledger.

    A fill is accepted only when both the order lifecycle and portfolio ledger
    can accept it. Validation is performed before mutating either component,
    preserving atomic behavior at the in-memory domain boundary.
    """

    def __init__(self, lifecycle: OrderLifecycle, ledger: PortfolioLedger) -> None:
        if lifecycle.order.environment.value not in {"RESEARCH", "BACKTEST", "WALK_FORWARD", "PAPER"}:
            raise ExecutionSessionError("UNSUPPORTED_EXECUTION_ENVIRONMENT")
        self._lifecycle = lifecycle
        self._ledger = ledger
        self._fill_ids: set[UUID] = set()

    @property
    def state(self) -> ExecutionSessionState:
        return ExecutionSessionState(self._lifecycle, self._ledger.state, frozenset(self._fill_ids))

    def apply_fill(self, fill: Fill) -> ExecutionSessionState:
        if fill.fill_id in self._fill_ids:
            raise ExecutionSessionError("DUPLICATE_FILL")

        # Validate lifecycle first. The ledger is only mutated after the
        # lifecycle accepts the fill, avoiding partial state transitions.
        try:
            next_lifecycle = self._lifecycle.apply_fill(fill)
        except OrderLifecycleError as exc:
            raise ExecutionSessionError(str(exc)) from exc

        try:
            next_portfolio = self._preview_ledger_fill(fill)
        except (ValueError, KeyError) as exc:
            raise ExecutionSessionError(str(exc)) from exc

        # Commit both domain transitions only after both validations pass.
        self._lifecycle = next_lifecycle
        self._ledger.apply_fill(fill)
        self._fill_ids.add(fill.fill_id)
        return ExecutionSessionState(self._lifecycle, next_portfolio, frozenset(self._fill_ids))

    def cancel(self) -> ExecutionSessionState:
        """Terminally cancel an open or partially filled order without changing portfolio state."""
        try:
            next_lifecycle = self._lifecycle.cancel()
        except OrderLifecycleError as exc:
            raise ExecutionSessionError(str(exc)) from exc
        self._lifecycle = next_lifecycle
        return self.state

    def reject(self) -> ExecutionSessionState:
        """Terminally reject an unfilled order without changing portfolio state."""
        try:
            next_lifecycle = self._lifecycle.reject()
        except OrderLifecycleError as exc:
            raise ExecutionSessionError(str(exc)) from exc
        self._lifecycle = next_lifecycle
        return self.state

    def _preview_ledger_fill(self, fill: Fill) -> PortfolioState:
        """Validate ledger acceptance without changing the live ledger state."""
        preview = deepcopy(self._ledger)
        return preview.apply_fill(fill)
