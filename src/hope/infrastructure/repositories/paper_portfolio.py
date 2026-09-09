from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, ForeignKey, MetaData, Numeric, Table, Uuid, BigInteger, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffectType
from hope.application.paper.fills import paper_fill_payload_hash
from hope.domain.execution.simulator import Fill
from hope.domain.portfolio.ledger import PortfolioLedger, PortfolioState, PositionState
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
        )
        self._effects = SqlAlchemyPaperEffectRepository(connection)

    def _assert_tracked_fill(self, fill: Fill) -> None:
        effect = self._effects.get(PaperEffectType.FILL, fill.fill_id)
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

    def load_ledger(self, portfolio_id: UUID) -> PortfolioLedger | None:
        portfolio = self._connection.execute(
            select(self._portfolios).where(self._portfolios.c.portfolio_id == portfolio_id)
        ).mappings().one_or_none()
        if portfolio is None:
            return None
        applications = self._connection.execute(
            select(
                self._applications.c.fill_id,
                self._applications.c.application_sequence,
            )
            .where(self._applications.c.portfolio_id == portfolio_id)
            .order_by(self._applications.c.application_sequence)
        ).mappings().all()
        expected_sequences = list(range(1, portfolio["version"] + 1))
        actual_sequences = [row["application_sequence"] for row in applications]
        if actual_sequences != expected_sequences:
            raise RuntimeError("PAPER_PORTFOLIO_APPLICATION_HISTORY_INCONSISTENT")
        state = PortfolioState(portfolio["cash"], self._load_positions(portfolio_id))
        return PortfolioLedger.from_state(
            state,
            applied_fill_ids=[row["fill_id"] for row in applications],
        )

    def apply_fill(self, portfolio_id: UUID, initial_cash: Decimal, fill: Fill) -> bool:
        """Atomically apply one already-durable PAPER fill to materialized portfolio state."""
        with self._connection.begin_nested():
            self._assert_tracked_fill(fill)
            portfolio = self._ensure_and_lock_portfolio(portfolio_id, initial_cash)
            existing = self._connection.execute(
                select(self._applications.c.fill_id).where(
                    self._applications.c.portfolio_id == portfolio_id,
                    self._applications.c.fill_id == fill.fill_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return False

            ledger = PortfolioLedger.from_state(
                PortfolioState(portfolio["cash"], self._load_positions(portfolio_id))
            )
            state = ledger.apply_fill(fill)
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
            return True
