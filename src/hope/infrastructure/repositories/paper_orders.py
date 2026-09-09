from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, MetaData, Numeric, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.orders import paper_order_payload_hash
from hope.domain.execution.models import Environment, Order
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


class SqlAlchemyPaperOrderRepository:
    """Persist PAPER orders together with their durable idempotency evidence."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._orders = Table(
            "orders",
            metadata,
            Column("order_id", Uuid, primary_key=True),
            Column("signal_id", Uuid, nullable=False),
            Column("instrument_id", Uuid, nullable=False),
            Column("environment", String, nullable=False),
            Column("side", String, nullable=False),
            Column("quantity", Numeric, nullable=False),
        )
        self._effects = SqlAlchemyPaperEffectRepository(connection)

    def _get_row(self, order_id: UUID):
        return self._connection.execute(
            select(
                self._orders.c.order_id,
                self._orders.c.signal_id,
                self._orders.c.instrument_id,
                self._orders.c.environment,
                self._orders.c.side,
                self._orders.c.quantity,
            ).where(self._orders.c.order_id == order_id)
        ).mappings().one_or_none()

    @staticmethod
    def _assert_order_row_matches(row, order: Order) -> None:
        if (
            row["order_id"] != order.order_id
            or row["signal_id"] != order.signal_id
            or row["instrument_id"] != order.instrument_id
            or row["environment"] != Environment.PAPER.value
            or row["side"] != order.side.value
            or row["quantity"] != order.quantity
        ):
            raise ValueError("PAPER_ORDER_IDENTITY_CONFLICT")

    def persist(self, effect: PaperEffect, order: Order) -> bool:
        """Atomically persist a PAPER order and its effect within a savepoint."""
        if order.environment is not Environment.PAPER:
            raise ValueError("PAPER_ORDER_REQUIRES_PAPER_ENVIRONMENT")
        if effect.effect_type is not PaperEffectType.ORDER or effect.entity_id != order.order_id:
            raise ValueError("PAPER_ORDER_EFFECT_MISMATCH")
        if effect.payload_hash != paper_order_payload_hash(order):
            raise ValueError("PAPER_ORDER_EFFECT_PAYLOAD_MISMATCH")
        if self._effects.get(PaperEffectType.SIGNAL, order.signal_id) is None:
            raise ValueError("PAPER_ORDER_SOURCE_SIGNAL_UNTRACKED")

        with self._connection.begin_nested():
            existing_effect = self._effects.get(PaperEffectType.ORDER, order.order_id)
            existing_order = self._get_row(order.order_id)
            if existing_effect is None and existing_order is not None:
                raise ValueError("PAPER_ORDER_UNTRACKED_DURABLE_STATE")

            recorded_effect = self._effects.record(effect)
            if not recorded_effect:
                row = existing_order if existing_order is not None else self._get_row(order.order_id)
                if row is None:
                    raise RuntimeError("PAPER_ORDER_EFFECT_WITHOUT_ORDER")
                self._assert_order_row_matches(row, order)
                return False

            inserted_id = self._connection.execute(
                pg_insert(self._orders)
                .values(
                    order_id=order.order_id,
                    signal_id=order.signal_id,
                    instrument_id=order.instrument_id,
                    environment=order.environment.value,
                    side=order.side.value,
                    quantity=order.quantity,
                )
                .on_conflict_do_nothing(index_elements=["order_id"])
                .returning(self._orders.c.order_id)
            ).scalar_one_or_none()
            if inserted_id is None:
                row = self._get_row(order.order_id)
                if row is not None:
                    self._assert_order_row_matches(row, order)
                raise ValueError("PAPER_ORDER_DURABLE_STATE_CONFLICT")
            return True
