from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, Connection, DateTime, MetaData, SmallInteger, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.domain.market_intelligence.models import (
    IntelligenceAction,
    IntelligenceCategory,
    IntelligenceMateriality,
    IntelligenceScope,
    IntelligenceSourceTier,
    MarketIntelligenceEvent,
)


class MarketIntelligenceEventRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    scope: IntelligenceScope
    instrument_id: UUID | None
    universe_version_id: UUID | None
    source: str
    source_item_id: str
    source_tier: IntelligenceSourceTier
    category: IntelligenceCategory
    materiality: IntelligenceMateriality
    recommended_action: IntelligenceAction
    event_time: datetime
    available_time: datetime
    ingestion_time: datetime
    source_payload_hash: str
    created_at: datetime


class SqlAlchemyMarketIntelligenceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._events = Table(
            "market_intelligence_events",
            metadata,
            Column("event_id", Uuid, primary_key=True),
            Column("scope", String, nullable=False),
            Column("instrument_id", Uuid, nullable=True),
            Column("universe_version_id", Uuid, nullable=True),
            Column("source", String, nullable=False),
            Column("source_item_id", String, nullable=False),
            Column("source_tier", SmallInteger, nullable=False),
            Column("category", String, nullable=False),
            Column("materiality", String, nullable=False),
            Column("recommended_action", String, nullable=False),
            Column("event_time", DateTime(timezone=True), nullable=False),
            Column("available_time", DateTime(timezone=True), nullable=False),
            Column("ingestion_time", DateTime(timezone=True), nullable=False),
            Column("source_payload_hash", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def persist(self, event: MarketIntelligenceEvent) -> bool:
        statement = (
            pg_insert(self._events)
            .values(
                event_id=event.event_id,
                scope=event.scope.value,
                instrument_id=event.instrument_id,
                universe_version_id=event.universe_version_id,
                source=event.source,
                source_item_id=event.source_item_id,
                source_tier=int(event.source_tier),
                category=event.category.value,
                materiality=event.materiality.value,
                recommended_action=event.recommended_action.value,
                event_time=event.event_time,
                available_time=event.available_time,
                ingestion_time=event.ingestion_time,
                source_payload_hash=event.source_payload_hash,
            )
            .on_conflict_do_nothing(index_elements=["source", "source_item_id"])
            .returning(self._events.c.event_id)
        )
        return self._connection.execute(statement).scalar_one_or_none() is not None

    def get(self, event_id: UUID) -> MarketIntelligenceEventRecord | None:
        row = self._connection.execute(
            select(self._events).where(self._events.c.event_id == event_id)
        ).mappings().one_or_none()
        if row is None:
            return None

        record = MarketIntelligenceEventRecord(**row)
        MarketIntelligenceEvent(**record.model_dump(exclude={"created_at"}))
        return record
