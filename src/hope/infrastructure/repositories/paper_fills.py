from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, Numeric, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.fills import paper_fill_payload_hash
from hope.domain.execution.simulator import Fill
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


class SqlAlchemyPaperFillRepository:
    """Persist PAPER fills together with durable idempotency evidence."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
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

    def _get_row(self, fill_id: UUID):
        return self._connection.execute(select(self._fills).where(self._fills.c.fill_id == fill_id)).mappings().one_or_none()

    @staticmethod
    def _assert_row_matches(row, fill: Fill) -> None:
        if (row["fill_id"] != fill.fill_id or row["order_id"] != fill.order_id or row["quantity"] != fill.quantity
            or row["fill_price"] != fill.price or row["slippage"] != fill.slippage
            or row["transaction_cost"] != fill.commission or row["filled_at"] != fill.fill_time):
            raise ValueError("PAPER_FILL_IDENTITY_CONFLICT")

    def persist(self, effect: PaperEffect, fill: Fill) -> bool:
        if effect.effect_type is not PaperEffectType.FILL or effect.entity_id != fill.fill_id:
            raise ValueError("PAPER_FILL_EFFECT_MISMATCH")
        if effect.payload_hash != paper_fill_payload_hash(fill):
            raise ValueError("PAPER_FILL_EFFECT_PAYLOAD_MISMATCH")
        if self._effects.get(PaperEffectType.ORDER, fill.order_id) is None:
            raise ValueError("PAPER_FILL_SOURCE_ORDER_UNTRACKED")

        with self._connection.begin_nested():
            existing_effect = self._effects.get(PaperEffectType.FILL, fill.fill_id)
            existing_fill = self._get_row(fill.fill_id)
            if existing_effect is None and existing_fill is not None:
                raise ValueError("PAPER_FILL_UNTRACKED_DURABLE_STATE")
            recorded = self._effects.record(effect)
            if not recorded:
                row = existing_fill if existing_fill is not None else self._get_row(fill.fill_id)
                if row is None:
                    raise RuntimeError("PAPER_FILL_EFFECT_WITHOUT_FILL")
                self._assert_row_matches(row, fill)
                return False

            inserted_id = self._connection.execute(
                pg_insert(self._fills).values(
                    fill_id=fill.fill_id, order_id=fill.order_id, quantity=fill.quantity,
                    fill_price=fill.price, slippage=fill.slippage, transaction_cost=fill.commission,
                    filled_at=fill.fill_time,
                ).on_conflict_do_nothing(index_elements=["fill_id"]).returning(self._fills.c.fill_id)
            ).scalar_one_or_none()
            if inserted_id is None:
                row = self._get_row(fill.fill_id)
                if row is not None:
                    self._assert_row_matches(row, fill)
                raise ValueError("PAPER_FILL_DURABLE_STATE_CONFLICT")
            return True
