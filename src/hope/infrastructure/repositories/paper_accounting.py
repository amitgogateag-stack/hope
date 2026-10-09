from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection

from hope.application.paper.context import PaperCycleContext
from hope.application.paper.effects import PaperEffectType
from hope.application.paper.portfolio_pnl import PaperPortfolioPnLWriter
from hope.domain.execution.simulator import Fill
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.paper_portfolio_pnl import SqlAlchemyPaperPortfolioPnLRepository


class SqlAlchemyPaperAccountingRepository:
    """Atomic durable PAPER fill -> portfolio -> transition-derived P&L boundary."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._jobs = SqlAlchemyJobRunRepository(connection)
        self._effects = SqlAlchemyPaperEffectRepository(connection)
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
            # Reconciliation takes the job lock before proving the portfolio.
            # Match that order so accounting cannot hold the portfolio while
            # waiting for reconciliation's job lock.
            if not self._jobs.lock_claimed(context.job_run):
                raise RuntimeError("PAPER_ACCOUNTING_JOB_NOT_CLAIMED")
            if (
                self._effects.get_reusable_for_job(
                    PaperEffectType.FILL,
                    fill.fill_id,
                    context.job_run.job_run_id,
                )
                is None
            ):
                raise ValueError("PAPER_ACCOUNTING_SOURCE_FILL_UNTRACKED")
            transition = self._portfolio.apply_fill_with_transition(
                portfolio_id,
                initial_cash,
                fill,
                job_run_id=context.job_run.job_run_id,
            )
            if transition is None:
                try:
                    event = self._pnl.get(portfolio_id, fill.fill_id)
                except RuntimeError as exc:
                    if str(exc) != "PAPER_PORTFOLIO_APPLIED_FILL_WITHOUT_PNL":
                        raise
                    raise RuntimeError(
                        "PAPER_ACCOUNTING_APPLIED_FILL_WITHOUT_PNL"
                    ) from exc
                if event is None:
                    raise RuntimeError("PAPER_ACCOUNTING_APPLIED_FILL_WITHOUT_PNL")
                replayed = self._portfolio.load_fill_transition(
                    portfolio_id,
                    fill.fill_id,
                    lock_for_update=True,
                    current_job_run_id=context.job_run.job_run_id,
                )
                if replayed is None:
                    raise RuntimeError(
                        "PAPER_ACCOUNTING_APPLIED_FILL_TRANSITION_NOT_DURABLE"
                    )
                if (
                    event.portfolio_id != portfolio_id
                    or event.fill_id != fill.fill_id
                    or event.instrument_id != fill.instrument_id
                    or event.event_time != fill.fill_time
                    or replayed.fill_id != event.fill_id
                    or replayed.instrument_id != event.instrument_id
                    or replayed.realized_pnl_delta != event.realized_pnl_delta
                    or replayed.commission_delta != event.commission_delta
                ):
                    raise ValueError("PAPER_ACCOUNTING_PNL_DURABLE_STATE_CONFLICT")
                if self._pnl_writer.record(
                    context,
                    portfolio_id,
                    fill,
                    replayed,
                ):
                    raise RuntimeError("PAPER_ACCOUNTING_RETRY_RECREATED_PNL")
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
