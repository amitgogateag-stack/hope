from __future__ import annotations

from uuid import UUID
from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, Uuid, insert, select
from sqlalchemy.engine import Connection
from hope.domain.identity.models import IdentityMapping, IdentityStatus, Instrument


class IdentityRepository:
    """Persistence adapter for canonical instruments and identity mappings."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._instruments = Table(
            "instruments", metadata,
            Column("instrument_id", Uuid, primary_key=True),
            Column("canonical_symbol", String, nullable=False),
            Column("exchange", String, nullable=False),
            Column("status", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self._broker = Table(
            "broker_instruments", metadata,
            Column("broker_instrument_id", String, primary_key=True),
            Column("broker", String, nullable=False),
            Column("instrument_id", Uuid),
            Column("status", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self._mappings = Table(
            "identity_mappings", metadata,
            Column("identity_mapping_id", Uuid, primary_key=True),
            Column("source_symbol", String, nullable=False),
            Column("broker_instrument_id", String, nullable=False),
            Column("canonical_instrument_id", Uuid),
            Column("status", String, nullable=False),
            Column("reason", String),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def add_instrument(self, instrument: Instrument, created_at) -> None:
        self._connection.execute(insert(self._instruments).values(
            instrument_id=instrument.instrument_id,
            canonical_symbol=instrument.canonical_symbol,
            exchange=instrument.exchange,
            status=instrument.status.value,
            created_at=created_at,
        ))

    def get_instrument(self, instrument_id: UUID) -> Instrument | None:
        row = self._connection.execute(
            select(self._instruments).where(self._instruments.c.instrument_id == instrument_id)
        ).mappings().one_or_none()
        return Instrument(**{k: row[k] for k in ("instrument_id", "canonical_symbol", "exchange", "status")}) if row else None

    def add_mapping(self, mapping: IdentityMapping, identity_mapping_id: UUID, created_at) -> None:
        self._connection.execute(insert(self._mappings).values(
            identity_mapping_id=identity_mapping_id,
            source_symbol=mapping.source_symbol,
            broker_instrument_id=mapping.broker_instrument_id,
            canonical_instrument_id=mapping.canonical_instrument_id,
            status=mapping.status.value,
            reason=mapping.reason,
            created_at=created_at,
        ))

    def get_mapping(self, source_symbol: str) -> IdentityMapping | None:
        row = self._connection.execute(
            select(self._mappings)
            .where(self._mappings.c.source_symbol == source_symbol)
            .order_by(self._mappings.c.created_at.desc())
            .limit(1)
        ).mappings().one_or_none()
        return IdentityMapping(**{k: row[k] for k in ("source_symbol", "broker_instrument_id", "canonical_instrument_id", "status", "reason")}) if row else None
