from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection

from hope.application.paper.context import PaperCycleContext
from hope.application.paper.portfolio_pnl import PaperPortfolioPnLWriter
from hope.domain.execution.simulator import Fill
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.paper_portfolio_pnl import SqlAlchemyPaperPortfolioPnLRepository


class SqlAlchemyPaperAccountingRepository:
    """Atomic durable PAPER fill -> portfolio -> transition-derived P&L boundary."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._portfolio = SqlAlchemyPaperPortfolioRepository(connection)
        self._pnl = SqlAlchemyPaperPortfolioPnLRepository(connection)
        self._pnl_writer = PaperPortfolioPnLWriter(self._pnl)

    def apply_fill(
        self,
        context: PaperCycleContext,
        portfolio_id: UUID,
        initial_cash: Decimal,
        fill: Fill,
    ) -> bool:
        with self._connection.begin_nested():
            transition = self._portfolio.apply_fill_with_transition(
                portfolio_id,
                initial_cash,
                fill,
            )
            if transition is None:
                event = self._pnl.get(portfolio_id, fill.fill_id)
                if event is None:
                    raise RuntimeError("PAPER_ACCOUNTING_APPLIED_FILL_WITHOUT_PNL")
                if event.instrument_id != fill.instrument_id or event.event_time != fill.fill_time:
                    raise ValueError("PAPER_ACCOUNTING_PNL_DURABLE_STATE_CONFLICT")
                return False

            recorded = self._pnl_writer.record(
                context,
                portfolio_id,
                fill,
                transition,
            )
            if not recorded:
                raise ValueError("PAPER_ACCOUNTING_PNL_PREEXISTED_NEW_TRANSITION")
            return True
