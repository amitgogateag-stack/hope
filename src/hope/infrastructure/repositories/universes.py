from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Uuid, insert, select
from sqlalchemy.engine import Connection


class UniverseVersionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    universe_version_id: UUID
    universe_id: UUID
    version: str = Field(min_length=1)
    pit_certified: bool
    declared_member_count: int = Field(ge=0)
    created_at: datetime | None = None


class UniverseRepository:
    def __init__(self, connection: Connection) -> None:
        metadata = MetaData()
        self._table = Table(
            "universe_versions", metadata,
            Column("universe_version_id", Uuid, primary_key=True),
            Column("universe_id", Uuid, nullable=False),
            Column("version", String, nullable=False),
            Column("pit_certified", Boolean, nullable=False),
            Column("declared_member_count", Integer, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self._connection = connection

    def create(self, record: UniverseVersionRecord) -> None:
        values = record.model_dump()
        values["created_at"] = values["created_at"] or datetime.now(timezone.utc)
        self._connection.execute(insert(self._table).values(**values))

    def get(self, universe_version_id: UUID) -> UniverseVersionRecord | None:
        row = self._connection.execute(
            select(self._table).where(self._table.c.universe_version_id == universe_version_id)
        ).mappings().one_or_none()
        return UniverseVersionRecord(**row) if row else None
