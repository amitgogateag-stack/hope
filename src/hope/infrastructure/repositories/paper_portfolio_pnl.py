from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, ForeignKey, MetaData, Numeric, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffect, PaperEffectType
from hope.application.paper.portfolio_pnl import (
    PaperPortfolioPnLEvent,
    paper_portfolio_pnl_event_id,
    paper_portfolio_pnl_payload_hash,
)
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository


class SqlAlchemyPaperPortfolioPnLRepository:
    """Persist transition-derived PAPER accounting events with durable lineage."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._events = Table(
            "paper_portfolio_pnl_events",
            metadata,
            Column("pnl_event_id", Uuid, primary_key=True),
            Column("portfolio_id", Uuid, ForeignKey("paper_portfolios.portfolio_id"), nullable=False),
            Column("fill_id", Uuid, ForeignKey("fills.fill_id"), nullable=False),
            Column("instrument_id", Uuid, ForeignKey("instruments.instrument_id"), nullable=False),
            Column("realized_pnl_delta", Numeric, nullable=False),
            Column("commission_delta", Numeric, nullable=False),
            Column("event_time", DateTime(timezone=True), nullable=False),
        )
        self._applications = Table(
            "paper_portfolio_fill_applications",
            metadata,
            Column("portfolio_id", Uuid, primary_key=True),
            Column("fill_id", Uuid, primary_key=True),
        )
        self._effects = SqlAlchemyPaperEffectRepository(connection)

    def _get_row(self, pnl_event_id: UUID):
        return self._connection.execute(
            select(self._events).where(self._events.c.pnl_event_id == pnl_event_id)
        ).mappings().one_or_none()

    @staticmethod
    def _event_from_row(row) -> PaperPortfolioPnLEvent:
        return PaperPortfolioPnLEvent(
            pnl_event_id=row["pnl_event_id"],
            portfolio_id=row["portfolio_id"],
            fill_id=row["fill_id"],
            instrument_id=row["instrument_id"],
            realized_pnl_delta=row["realized_pnl_delta"],
            commission_delta=row["commission_delta"],
            event_time=row["event_time"],
        )

    @staticmethod
    def _assert_row_matches(row, event: PaperPortfolioPnLEvent) -> None:
        if (
            row["pnl_event_id"] != event.pnl_event_id
            or row["portfolio_id"] != event.portfolio_id
            or row["fill_id"] != event.fill_id
            or row["instrument_id"] != event.instrument_id
            or row["realized_pnl_delta"] != event.realized_pnl_delta
            or row["commission_delta"] != event.commission_delta
            or row["event_time"] != event.event_time
        ):
            raise ValueError("PAPER_PORTFOLIO_PNL_IDENTITY_CONFLICT")

    def get(self, portfolio_id: UUID, fill_id: UUID) -> PaperPortfolioPnLEvent | None:
        event_id = paper_portfolio_pnl_event_id(portfolio_id, fill_id)
        row = self._get_row(event_id)
        if row is None:
            return None
        event = self._event_from_row(row)
        self._assert_row_matches(row, event)
        effect = self._effects.get(PaperEffectType.PNL, event.pnl_event_id)
        if effect is None:
            raise RuntimeError("PAPER_PORTFOLIO_PNL_EVENT_WITHOUT_EFFECT")
        if effect.payload_hash != paper_portfolio_pnl_payload_hash(event):
            raise ValueError("PAPER_PORTFOLIO_PNL_EFFECT_PAYLOAD_CONFLICT")
        return event

    def persist(self, effect: PaperEffect, event: PaperPortfolioPnLEvent) -> bool:
        if effect.effect_type is not PaperEffectType.PNL or effect.entity_id != event.pnl_event_id:
            raise ValueError("PAPER_PORTFOLIO_PNL_EFFECT_MISMATCH")
        if effect.payload_hash != paper_portfolio_pnl_payload_hash(event):
            raise ValueError("PAPER_PORTFOLIO_PNL_EFFECT_PAYLOAD_MISMATCH")
        if event.commission_delta < 0:
            raise ValueError("PAPER_PORTFOLIO_PNL_COMMISSION_INVALID")

        with self._connection.begin_nested():
            application = self._connection.execute(
                select(self._applications.c.fill_id).where(
                    self._applications.c.portfolio_id == event.portfolio_id,
                    self._applications.c.fill_id == event.fill_id,
                )
            ).scalar_one_or_none()
            if application is None:
                raise ValueError("PAPER_PORTFOLIO_PNL_FILL_NOT_APPLIED")

            existing_effect = self._effects.get(PaperEffectType.PNL, event.pnl_event_id)
            existing_event = self._get_row(event.pnl_event_id)
            if existing_effect is None and existing_event is not None:
                raise ValueError("PAPER_PORTFOLIO_PNL_UNTRACKED_DURABLE_STATE")

            recorded_effect = self._effects.record(effect)
            if not recorded_effect:
                row = existing_event if existing_event is not None else self._get_row(event.pnl_event_id)
                if row is None:
                    raise RuntimeError("PAPER_PORTFOLIO_PNL_EFFECT_WITHOUT_EVENT")
                self._assert_row_matches(row, event)
                return False

            inserted = self._connection.execute(
                pg_insert(self._events)
                .values(
                    pnl_event_id=event.pnl_event_id,
                    portfolio_id=event.portfolio_id,
                    fill_id=event.fill_id,
                    instrument_id=event.instrument_id,
                    realized_pnl_delta=event.realized_pnl_delta,
                    commission_delta=event.commission_delta,
                    event_time=event.event_time,
                )
                .on_conflict_do_nothing(index_elements=["pnl_event_id"])
                .returning(self._events.c.pnl_event_id)
            ).scalar_one_or_none()
            if inserted is None:
                row = self._get_row(event.pnl_event_id)
                if row is not None:
                    self._assert_row_matches(row, event)
                raise ValueError("PAPER_PORTFOLIO_PNL_DURABLE_STATE_CONFLICT")
            return True
