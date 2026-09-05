from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import Column, DateTime, MetaData, Table, Uuid, insert, select
from sqlalchemy.engine import Connection
from hope.domain.universe.models import UniverseMember


class UniverseMemberRepository:
    """Append-only persistence adapter for published universe membership."""

    def __init__(self, connection: Connection) -> None:
        metadata = MetaData()
        self._table = Table(
            "universe_members", metadata,
            Column("universe_version_id", Uuid, primary_key=True),
            Column("instrument_id", Uuid, primary_key=True),
            Column("valid_from", DateTime(timezone=True)),
            Column("valid_to", DateTime(timezone=True)),
        )
        self._connection = connection

    def add(self, universe_version_id: UUID, member: UniverseMember) -> None:
        self._connection.execute(insert(self._table).values(
            universe_version_id=universe_version_id,
            instrument_id=member.instrument_id,
            valid_from=member.valid_from,
            valid_to=member.valid_to,
        ))

    def list(self, universe_version_id: UUID) -> tuple[UniverseMember, ...]:
        rows = self._connection.execute(
            select(self._table)
            .where(self._table.c.universe_version_id == universe_version_id)
            .order_by(self._table.c.instrument_id)
        ).mappings().all()
        def normalize(value):
            if value is not None and value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        return tuple(
            UniverseMember(
                instrument_id=row["instrument_id"],
                valid_from=normalize(row["valid_from"]),
                valid_to=normalize(row["valid_to"]),
            )
            for row in rows
        )
