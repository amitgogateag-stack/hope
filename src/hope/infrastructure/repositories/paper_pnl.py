from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, Numeric, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.pnl import PaperPnLEvent, paper_pnl_payload_hash
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


class SqlAlchemyPaperPnLRepository:
    """Persist PAPER P&L events with durable idempotency evidence."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._pnl_events = Table(
            "pnl_events",
            metadata,
            Column("pnl_event_id", Uuid, primary_key=True),
            Column("position_id", Uuid, nullable=False),
            Column("amount", Numeric, nullable=False),
            Column("event_time", DateTime(timezone=True), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self._positions = Table(
            "positions",
            metadata,
            Column("position_id", Uuid, primary_key=True),
            Column("instrument_id", Uuid, nullable=False),
            Column("opened_from_signal_id", Uuid, nullable=False),
            Column("quantity", Numeric, nullable=False),
            Column("opened_at", DateTime(timezone=True), nullable=False),
            Column("closed_at", DateTime(timezone=True), nullable=True),
        )
        self._effects = SqlAlchemyPaperEffectRepository(connection)

    def _get_row(self, pnl_event_id: UUID):
        return self._connection.execute(
            select(
                self._pnl_events.c.pnl_event_id,
                self._pnl_events.c.position_id,
                self._pnl_events.c.amount,
                self._pnl_events.c.event_time,
            ).where(self._pnl_events.c.pnl_event_id == pnl_event_id)
        ).mappings().one_or_none()

    @staticmethod
    def _assert_row_matches(row, event: PaperPnLEvent) -> None:
        if (
            row["pnl_event_id"] != event.pnl_event_id
            or row["position_id"] != event.position_id
            or row["amount"] != event.amount
            or row["event_time"] != event.event_time
        ):
            raise ValueError("PAPER_PNL_IDENTITY_CONFLICT")

    def _assert_position_has_paper_lineage(self, position_id: UUID) -> None:
        position = self._connection.execute(
            select(self._positions.c.opened_from_signal_id).where(
                self._positions.c.position_id == position_id
            )
        ).mappings().one_or_none()
        if position is None:
            raise ValueError("PAPER_PNL_POSITION_NOT_FOUND")
        signal_id = position["opened_from_signal_id"]
        if signal_id is None:
            raise ValueError("PAPER_PNL_POSITION_SIGNAL_MISSING")
        if self._effects.get(PaperEffectType.SIGNAL, signal_id) is None:
            raise ValueError("PAPER_PNL_POSITION_UNTRACKED_SIGNAL")

    def persist(self, effect: PaperEffect, event: PaperPnLEvent) -> bool:
        if effect.effect_type is not PaperEffectType.PNL or effect.entity_id != event.pnl_event_id:
            raise ValueError("PAPER_PNL_EFFECT_MISMATCH")
        if effect.payload_hash != paper_pnl_payload_hash(event):
            raise ValueError("PAPER_PNL_EFFECT_PAYLOAD_MISMATCH")

        with self._connection.begin_nested():
            self._assert_position_has_paper_lineage(event.position_id)
            existing_effect = self._effects.get(PaperEffectType.PNL, event.pnl_event_id)
            existing_event = self._get_row(event.pnl_event_id)
            if existing_effect is None and existing_event is not None:
                raise ValueError("PAPER_PNL_UNTRACKED_DURABLE_STATE")

            recorded_effect = self._effects.record(effect)
            if not recorded_effect:
                row = existing_event if existing_event is not None else self._get_row(event.pnl_event_id)
                if row is None:
                    raise RuntimeError("PAPER_PNL_EFFECT_WITHOUT_EVENT")
                self._assert_row_matches(row, event)
                return False

            inserted_id = self._connection.execute(
                pg_insert(self._pnl_events)
                .values(
                    pnl_event_id=event.pnl_event_id,
                    position_id=event.position_id,
                    amount=event.amount,
                    event_time=event.event_time,
                )
                .on_conflict_do_nothing(index_elements=["pnl_event_id"])
                .returning(self._pnl_events.c.pnl_event_id)
            ).scalar_one_or_none()
            if inserted_id is None:
                row = self._get_row(event.pnl_event_id)
                if row is not None:
                    self._assert_row_matches(row, event)
                raise ValueError("PAPER_PNL_DURABLE_STATE_CONFLICT")
            return True
