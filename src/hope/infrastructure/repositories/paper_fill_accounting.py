from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection

from hope.application.paper.accounting import PaperAccountingWriter
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.fills import PaperFillWriter
from hope.domain.execution.simulator import Fill
from hope.infrastructure.repositories.paper_accounting import SqlAlchemyPaperAccountingRepository
from hope.infrastructure.repositories.paper_fills import SqlAlchemyPaperFillRepository


class SqlAlchemyPaperFillAccountingRepository:
    """Atomically persist one PAPER fill and its authoritative accounting effects."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._fill_writer = PaperFillWriter(SqlAlchemyPaperFillRepository(connection))
        self._accounting_writer = PaperAccountingWriter(SqlAlchemyPaperAccountingRepository(connection))

    def persist(
        self,
        context: PaperCycleContext,
        portfolio_id: UUID,
        initial_cash: Decimal,
        fill: Fill,
        *,
        sequence: int,
    ) -> bool:
        with self._connection.begin_nested():
            fill_recorded = self._fill_writer.record(context, fill, sequence=sequence)
            accounting_recorded = self._accounting_writer.apply_fill(
                context,
                portfolio_id,
                initial_cash,
                fill,
            )

            if fill_recorded != accounting_recorded:
                if not fill_recorded:
                    raise RuntimeError("PAPER_FILL_ACCOUNTING_PREEXISTING_FILL_WITHOUT_ACCOUNTING")
                raise RuntimeError("PAPER_FILL_ACCOUNTING_NEW_FILL_WITH_PREEXISTING_ACCOUNTING")

            return fill_recorded
