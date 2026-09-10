from __future__ import annotations

from decimal import Decimal
from typing import Protocol
from uuid import UUID

from hope.application.paper.context import PaperCycleContext
from hope.domain.execution.simulator import Fill


class PaperAccountingPersistence(Protocol):
    def apply_fill(
        self,
        context: PaperCycleContext,
        portfolio_id: UUID,
        initial_cash: Decimal,
        fill: Fill,
    ) -> bool:
        ...


class PaperAccountingWriter:
    """Atomic PAPER fill -> portfolio -> derived-P&L application boundary."""

    def __init__(self, repository: PaperAccountingPersistence) -> None:
        self._repository = repository

    def apply_fill(
        self,
        context: PaperCycleContext,
        portfolio_id: UUID,
        initial_cash: Decimal,
        fill: Fill,
    ) -> bool:
        return self._repository.apply_fill(context, portfolio_id, initial_cash, fill)
