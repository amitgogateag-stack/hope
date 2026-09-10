from __future__ import annotations

from decimal import Decimal
from typing import Protocol
from uuid import UUID

from hope.application.paper.context import PaperCycleContext
from hope.domain.execution.simulator import Fill


class PaperFillAccountingPersistence(Protocol):
    def persist(
        self,
        context: PaperCycleContext,
        portfolio_id: UUID,
        initial_cash: Decimal,
        fill: Fill,
        *,
        sequence: int,
    ) -> bool:
        ...


class PaperFillAccountingWriter:
    """Authoritative PAPER runtime boundary for durable fill plus accounting."""

    def __init__(self, repository: PaperFillAccountingPersistence) -> None:
        self._repository = repository

    def record(
        self,
        context: PaperCycleContext,
        portfolio_id: UUID,
        initial_cash: Decimal,
        fill: Fill,
        *,
        sequence: int,
    ) -> bool:
        return self._repository.persist(
            context,
            portfolio_id,
            initial_cash,
            fill,
            sequence=sequence,
        )
