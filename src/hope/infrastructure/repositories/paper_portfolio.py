from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, ForeignKey, MetaData, Numeric, String, Table, Uuid, BigInteger, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffectType
from hope.application.paper.fills import paper_fill_payload_hash
from hope.application.paper.portfolio_pnl import paper_portfolio_pnl_event_id
from hope.domain.execution.models import Environment, OrderSide
from hope.domain.execution.simulator import Fill
from hope.domain.portfolio.ledger import PortfolioFillTransition, PortfolioLedger, PortfolioState, PositionState
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


class SqlAlchemyPaperPortfolioRepository:
    """Materialize restart-safe PAPER portfolio state from durable tracked fills."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._portfolios = Table(
            "paper_portfolios", metadata,
            Column("portfolio_id", Uuid, primary_key=True),
            Column("initial_cash", Numeric, nullable=False),
            Column("cash", Numeric, nullable=False),
            Column("version", BigInteger, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )
        self._positions = Table(
            "paper_portfolio_positions", metadata,
            Column("portfolio_id", Uuid, ForeignKey("paper_portfolios.portfolio_id"), primary_key=True),
            Column("instrument_id", Uuid, primary_key=True),
            Column("quantity", Numeric, nullable=False),
            Column("average_price", Numeric, nullable=False),
            Column("realized_pnl", Numeric, nullable=False),
            Column("total_commission", Numeric, nullable=False),
        )
        self._applications = Table(
            "paper_portfolio_fill_applications", metadata,
            Column("portfolio_id", Uuid, ForeignKey("paper_portfolios.portfolio_id"), primary_key=True),
            Column("fill_id", Uuid, primary_key=True),
            Column("application_sequence", BigInteger, nullable=False),
            Column("applied_at", DateTime(timezone=True), nullable=False),
        )
        self._fills = Table(
            "fills", metadata,
            Column("fill_id", Uuid, primary_key=True),
            Column("order_id", Uuid, nullable=False),
            Column("quantity", Numeric, nullable=False),
            Column("fill_price", Numeric, nullable=False),
            Column("slippage", Numeric, nullable=False),
            Column("transaction_cost", Numeric, nullable=False),
            Column("filled_at", DateTime(timezone=True), nullable=False),
            Column("cost_model_version", String, nullable=True),
        )
        self._orders = Table(
            "orders", metadata,
            Column("order_id", Uuid, primary_key=True),
            Column("signal_id", Uuid, nullable=False),
            Column("instrument_id", Uuid, nullable=False),
            Column("environment", String, nullable=False),
            Column("side", String, nullable=False),
        )
        self._effects = SqlAlchemyPaperEffectRepository(connection)

    def _assert_tracked_fill(self, fill: Fill, job_run_id: UUID) -> None:
        effect = self._effects.get_reusable_for_job(
            PaperEffectType.FILL,
            fill.fill_id,
            job_run_id,
        )
        if effect is None:
            raise ValueError("PAPER_PORTFOLIO_FILL_UNTRACKED")
        if effect.payload_hash != paper_fill_payload_hash(fill):
            raise ValueError("PAPER_PORTFOLIO_FILL_PAYLOAD_MISMATCH")
        row = self._connection.execute(
            select(self._fills).where(self._fills.c.fill_id == fill.fill_id)
        ).mappings().one_or_none()
        if row is None:
            raise RuntimeError("PAPER_PORTFOLIO_FILL_EFFECT_WITHOUT_FILL")
        if (
            row["order_id"] != fill.order_id
            or row["quantity"] != fill.quantity
            or row["fill_price"] != fill.price
            or row["slippage"] != fill.slippage
            or row["transaction_cost"] != fill.commission
            or row["filled_at"] != fill.fill_time
            or row["cost_model_version"] != fill.cost_model_version
        ):
            raise ValueError("PAPER_PORTFOLIO_FILL_DURABLE_STATE_CONFLICT")

    def _ensure_and_lock_portfolio(self, portfolio_id: UUID, initial_cash: Decimal):
        if not initial_cash.is_finite() or initial_cash < 0:
            raise ValueError("PAPER_PORTFOLIO_INITIAL_CASH_INVALID")
        self._connection.execute(
            pg_insert(self._portfolios)
            .values(portfolio_id=portfolio_id, initial_cash=initial_cash, cash=initial_cash, version=0)
            .on_conflict_do_nothing(index_elements=["portfolio_id"])
        )
        row = self._connection.execute(
            select(self._portfolios)
            .where(self._portfolios.c.portfolio_id == portfolio_id)
            .with_for_update()
        ).mappings().one()
        if row["initial_cash"] != initial_cash:
            raise ValueError("PAPER_PORTFOLIO_INITIAL_CASH_CONFLICT")
        return row

    def _load_positions(self, portfolio_id: UUID) -> dict[UUID, PositionState]:
        rows = self._connection.execute(
            select(self._positions).where(self._positions.c.portfolio_id == portfolio_id)
        ).mappings().all()
        return {
            row["instrument_id"]: PositionState(
                instrument_id=row["instrument_id"],
                quantity=row["quantity"],
                average_price=row["average_price"],
                realized_pnl=row["realized_pnl"],
                total_commission=row["total_commission"],
            )
            for row in rows
        }

    def _load_application_history(self, portfolio_id: UUID, version: int):
        applications = self._connection.execute(
            select(
                self._applications.c.fill_id,
                self._applications.c.application_sequence,
                self._applications.c.applied_at,
                self._fills.c.filled_at,
                self._fills.c.order_id,
                self._fills.c.quantity,
                self._fills.c.fill_price,
                self._fills.c.slippage,
                self._fills.c.transaction_cost,
                self._fills.c.cost_model_version,
                self._orders.c.signal_id,
                self._orders.c.instrument_id,
                self._orders.c.environment,
                self._orders.c.side,
            )
            .select_from(
                self._applications.join(
                    self._fills,
                    self._applications.c.fill_id == self._fills.c.fill_id,
                ).join(
                    self._orders,
                    self._fills.c.order_id == self._orders.c.order_id,
                )
            )
            .where(self._applications.c.portfolio_id == portfolio_id)
            .order_by(self._applications.c.application_sequence)
        ).mappings().all()
        expected_sequences = list(range(1, version + 1))
        actual_sequences = [row["application_sequence"] for row in applications]
        if actual_sequences != expected_sequences:
            raise RuntimeError("PAPER_PORTFOLIO_APPLICATION_HISTORY_INCONSISTENT")
        for row in applications:
            if row["applied_at"] < row["filled_at"]:
                raise RuntimeError("PAPER_PORTFOLIO_APPLICATION_PRECEDES_FILL")
        for previous, current in zip(applications, applications[1:]):
            if current["filled_at"] < previous["filled_at"]:
                raise RuntimeError("PAPER_PORTFOLIO_APPLICATION_TIME_REGRESSION")
        return applications

    def _restore_verified_ledger(self, portfolio, applications) -> PortfolioLedger:
        ledger = PortfolioLedger(portfolio["initial_cash"])
        for row in applications:
            if row["environment"] != Environment.PAPER.value:
                raise RuntimeError("PAPER_PORTFOLIO_NON_PAPER_HISTORY")
            fill = Fill(
                fill_id=row["fill_id"],
                order_id=row["order_id"],
                signal_id=row["signal_id"],
                instrument_id=row["instrument_id"],
                side=OrderSide(row["side"]),
                quantity=row["quantity"],
                price=row["fill_price"],
                commission=row["transaction_cost"],
                slippage=row["slippage"],
                cost_model_version=row["cost_model_version"],
                fill_time=row["filled_at"],
            )
            effect = self._effects.get(PaperEffectType.FILL, fill.fill_id)
            if effect is None:
                raise RuntimeError("PAPER_PORTFOLIO_APPLIED_FILL_WITHOUT_EFFECT")
            if effect.payload_hash != paper_fill_payload_hash(fill):
                raise ValueError("PAPER_PORTFOLIO_APPLIED_FILL_PAYLOAD_MISMATCH")
            pnl_effect = self._effects.get(
                PaperEffectType.PNL,
                paper_portfolio_pnl_event_id(portfolio["portfolio_id"], fill.fill_id),
            )
            if pnl_effect is not None:
                try:
                    reusable_fill = self._effects.get_reusable_for_job(
                        PaperEffectType.FILL,
                        fill.fill_id,
                        pnl_effect.job_run_id,
                    )
                except ValueError as exc:
                    raise ValueError(
                        "PAPER_PORTFOLIO_FILL_PNL_RUN_LINEAGE_MISMATCH"
                    ) from exc
                if reusable_fill is None:
                    raise ValueError("PAPER_PORTFOLIO_FILL_PNL_RUN_LINEAGE_MISMATCH")
            ledger.apply_fill(fill)

        materialized = PortfolioState(
            portfolio["cash"],
            self._load_positions(portfolio["portfolio_id"]),
        )
        if ledger.state != materialized:
            raise RuntimeError("PAPER_PORTFOLIO_MATERIALIZED_STATE_INCONSISTENT")
        return ledger

    def _load_materialized_ledger(
        self,
        portfolio_id: UUID,
        *,
        lock_for_update: bool = False,
    ) -> PortfolioLedger | None:
        statement = select(self._portfolios).where(
            self._portfolios.c.portfolio_id == portfolio_id
        )
        if lock_for_update:
            statement = statement.with_for_update()
        portfolio = self._connection.execute(
            statement
        ).mappings().one_or_none()
        if portfolio is None:
            return None
        applications = self._load_application_history(portfolio_id, portfolio["version"])
        return self._restore_verified_ledger(portfolio, applications)

    def load_ledger(
        self,
        portfolio_id: UUID,
        *,
        lock_for_update: bool = False,
    ) -> PortfolioLedger | None:
        """Recover a ledger only from complete, verified accounting history."""
        return self.verify_accounting_history(
            portfolio_id,
            lock_for_update=lock_for_update,
        )

    def load_fill_transition(
        self,
        portfolio_id: UUID,
        fill_id: UUID,
        *,
        lock_for_update: bool = False,
        current_job_run_id: UUID | None = None,
    ) -> PortfolioFillTransition | None:
        """Return a transition only from fully verified durable accounting history."""
        ledger = self.verify_accounting_history(
            portfolio_id,
            lock_for_update=lock_for_update,
            current_job_run_id=current_job_run_id,
        )
        if ledger is None:
            return None
        return ledger.transition_for_fill(fill_id)

    def _require_recoverable_execution_effect(
        self,
        effect_type: PaperEffectType,
        entity_id: UUID,
        *,
        current_job_run_id: UUID | None,
    ) -> None:
        try:
            if current_job_run_id is None:
                effect = self._effects.get_recoverable(effect_type, entity_id)
            else:
                effect = self._effects.get_reusable_for_job(
                    effect_type,
                    entity_id,
                    current_job_run_id,
                )
        except ValueError as exc:
            raise RuntimeError(
                "PAPER_PORTFOLIO_EXECUTION_OWNER_NOT_RECOVERABLE"
            ) from exc
        if effect is None:
            raise RuntimeError("PAPER_PORTFOLIO_EXECUTION_EFFECT_NOT_RECOVERABLE")

    def verify_accounting_history(
        self,
        portfolio_id: UUID,
        *,
        lock_for_update: bool = False,
        current_job_run_id: UUID | None = None,
    ) -> PortfolioLedger | None:
        """Restore a portfolio only from recoverable execution and accounting truth.

        Public recovery requires every source execution and PNL effect owner to be
        SUCCEEDED. A caller executing an authenticated job may additionally verify
        effects owned by that same in-flight job, which is required to prove its
        accounting before the lifecycle can atomically transition from CLAIMED to
        SUCCEEDED.
        """
        ledger = self._load_materialized_ledger(
            portfolio_id,
            lock_for_update=lock_for_update,
        )
        if ledger is None:
            return None

        from hope.infrastructure.repositories.paper_portfolio_pnl import (
            SqlAlchemyPaperPortfolioPnLRepository,
        )

        rows = self._connection.execute(
            select(
                self._applications.c.fill_id,
                self._fills.c.filled_at,
                self._fills.c.order_id,
                self._orders.c.signal_id,
            )
            .select_from(
                self._applications.join(
                    self._fills,
                    self._applications.c.fill_id == self._fills.c.fill_id,
                ).join(self._orders, self._fills.c.order_id == self._orders.c.order_id)
            )
            .where(self._applications.c.portfolio_id == portfolio_id)
        ).mappings().all()
        if {row["fill_id"] for row in rows} != set(ledger.applied_fill_ids):
            raise RuntimeError("PAPER_PORTFOLIO_ACCOUNTING_HISTORY_INCONSISTENT")

        pnl_repository = SqlAlchemyPaperPortfolioPnLRepository(self._connection)
        for row in rows:
            fill_id = row["fill_id"]
            event = pnl_repository.get(portfolio_id, fill_id)
            if event is None:
                raise RuntimeError("PAPER_PORTFOLIO_APPLIED_FILL_WITHOUT_PNL")
            try:
                if current_job_run_id is None:
                    recoverable_pnl = self._effects.get_recoverable(
                        PaperEffectType.PNL,
                        event.pnl_event_id,
                    )
                else:
                    recoverable_pnl = self._effects.get_reusable_for_job(
                        PaperEffectType.PNL,
                        event.pnl_event_id,
                        current_job_run_id,
                    )
            except ValueError as exc:
                raise RuntimeError(
                    "PAPER_PORTFOLIO_PNL_OWNER_NOT_RECOVERABLE"
                ) from exc
            if recoverable_pnl is None:
                raise RuntimeError("PAPER_PORTFOLIO_PNL_EFFECT_NOT_RECOVERABLE")
            for effect_type, entity_id in (
                (PaperEffectType.SIGNAL, row["signal_id"]),
                (PaperEffectType.ORDER, row["order_id"]),
                (PaperEffectType.FILL, fill_id),
            ):
                self._require_recoverable_execution_effect(
                    effect_type,
                    entity_id,
                    current_job_run_id=current_job_run_id,
                )
            transition = ledger.transition_for_fill(fill_id)
            if transition is None:
                raise RuntimeError("PAPER_PORTFOLIO_ACCOUNTING_TRANSITION_MISSING")
            if (
                event.instrument_id != transition.instrument_id
                or event.realized_pnl_delta != transition.realized_pnl_delta
                or event.commission_delta != transition.commission_delta
                or event.event_time != row["filled_at"]
            ):
                raise RuntimeError("PAPER_PORTFOLIO_ACCOUNTING_HISTORY_MISMATCH")
        return ledger

    def apply_fill_with_transition(
        self,
        portfolio_id: UUID,
        initial_cash: Decimal,
        fill: Fill,
        *,
        job_run_id: UUID,
    ) -> PortfolioFillTransition | None:
        """Atomically apply one durable PAPER fill and return its accounting transition."""
        with self._connection.begin_nested():
            self._assert_tracked_fill(fill, job_run_id)
            portfolio = self._ensure_and_lock_portfolio(portfolio_id, initial_cash)
            applications = self._load_application_history(portfolio_id, portfolio["version"])
            ledger = self._restore_verified_ledger(portfolio, applications)
            if any(row["fill_id"] == fill.fill_id for row in applications):
                return None
            if applications and fill.fill_time < applications[-1]["filled_at"]:
                raise ValueError("PAPER_PORTFOLIO_FILL_TIME_REGRESSION")

            transition = ledger.apply_fill_with_transition(fill)
            state = transition.state_after
            position = state.positions[fill.instrument_id]
            next_version = portfolio["version"] + 1

            self._connection.execute(
                update(self._portfolios)
                .where(self._portfolios.c.portfolio_id == portfolio_id)
                .values(cash=state.cash, version=next_version)
            )
            self._connection.execute(
                pg_insert(self._positions)
                .values(
                    portfolio_id=portfolio_id,
                    instrument_id=position.instrument_id,
                    quantity=position.quantity,
                    average_price=position.average_price,
                    realized_pnl=position.realized_pnl,
                    total_commission=position.total_commission,
                )
                .on_conflict_do_update(
                    index_elements=["portfolio_id", "instrument_id"],
                    set_={
                        "quantity": position.quantity,
                        "average_price": position.average_price,
                        "realized_pnl": position.realized_pnl,
                        "total_commission": position.total_commission,
                    },
                )
            )
            inserted = self._connection.execute(
                pg_insert(self._applications)
                .values(
                    portfolio_id=portfolio_id,
                    fill_id=fill.fill_id,
                    application_sequence=next_version,
                )
                .on_conflict_do_nothing(index_elements=["portfolio_id", "fill_id"])
                .returning(self._applications.c.fill_id)
            ).scalar_one_or_none()
            if inserted is None:
                raise ValueError("PAPER_PORTFOLIO_FILL_APPLICATION_CONFLICT")
            return transition

    def apply_fill(
        self,
        portfolio_id: UUID,
        initial_cash: Decimal,
        fill: Fill,
        *,
        job_run_id: UUID,
    ) -> bool:
        """Boolean wrapper around the ownership-authenticated transition API."""
        return (
            self.apply_fill_with_transition(
                portfolio_id,
                initial_cash,
                fill,
                job_run_id=job_run_id,
            )
            is not None
        )
