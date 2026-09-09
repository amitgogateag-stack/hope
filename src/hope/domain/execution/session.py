from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
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
    last_fill_time: datetime | None = None


class ExecutionSession:
    """Coordinates one order lifecycle with the portfolio ledger.

    A fill is accepted only when both the order lifecycle and portfolio ledger
    can accept it. Validation is performed before mutating either component,
    preserving atomic behavior at the in-memory domain boundary. Fill event time
    is monotonic within the session so a restored session cannot accept history
    that predates its last replayed fill.
    """

    def __init__(self, lifecycle: OrderLifecycle, ledger: PortfolioLedger) -> None:
        if lifecycle.order.environment.value not in {"RESEARCH", "BACKTEST", "WALK_FORWARD", "PAPER"}:
            raise ExecutionSessionError("UNSUPPORTED_EXECUTION_ENVIRONMENT")
        self._lifecycle = lifecycle
        self._ledger = ledger
        self._fill_ids: set[UUID] = set()
        self._last_fill_time: datetime | None = None

    @property
    def state(self) -> ExecutionSessionState:
        return ExecutionSessionState(
            self._lifecycle,
            self._ledger.state,
            frozenset(self._fill_ids),
            self._last_fill_time,
        )

    def apply_fill(self, fill: Fill) -> ExecutionSessionState:
        if fill.fill_id in self._fill_ids:
            raise ExecutionSessionError("DUPLICATE_FILL")
        if self._last_fill_time is not None and fill.fill_time < self._last_fill_time:
            raise ExecutionSessionError("FILL_EVENTS_OUT_OF_TIME_ORDER")

        try:
            next_lifecycle = self._lifecycle.apply_fill(fill)
        except OrderLifecycleError as exc:
            raise ExecutionSessionError(str(exc)) from exc

        try:
            next_portfolio = self._preview_ledger_fill(fill)
        except (ValueError, KeyError) as exc:
            raise ExecutionSessionError(str(exc)) from exc

        self._lifecycle = next_lifecycle
        self._ledger.apply_fill(fill)
        self._fill_ids.add(fill.fill_id)
        self._last_fill_time = fill.fill_time
        return self.state

    def cancel(self) -> ExecutionSessionState:
        try:
            next_lifecycle = self._lifecycle.cancel()
        except OrderLifecycleError as exc:
            raise ExecutionSessionError(str(exc)) from exc
        self._lifecycle = next_lifecycle
        return self.state

    def reject(self) -> ExecutionSessionState:
        try:
            next_lifecycle = self._lifecycle.reject()
        except OrderLifecycleError as exc:
            raise ExecutionSessionError(str(exc)) from exc
        self._lifecycle = next_lifecycle
        return self.state

    def _preview_ledger_fill(self, fill: Fill) -> PortfolioState:
        preview = deepcopy(self._ledger)
        return preview.apply_fill(fill)
