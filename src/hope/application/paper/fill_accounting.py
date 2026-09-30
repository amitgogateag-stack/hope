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
        if not isinstance(portfolio_id, UUID):
            raise ValueError("PAPER_ACCOUNTING_PORTFOLIO_ID_INVALID")
        if not initial_cash.is_finite() or initial_cash < 0:
            raise ValueError("PAPER_ACCOUNTING_INITIAL_CASH_INVALID")

        expected_order_id = context.order_id(fill.signal_id)
        if fill.order_id != expected_order_id:
            raise ValueError("PAPER_FILL_ORDER_IDENTITY_MISMATCH")
        expected_fill_id = context.fill_id(fill.signal_id, sequence)
        if fill.fill_id != expected_fill_id:
            raise ValueError("PAPER_FILL_IDENTITY_MISMATCH")

        return self._repository.persist(
            context,
            portfolio_id,
            initial_cash,
            fill,
            sequence=sequence,
        )
