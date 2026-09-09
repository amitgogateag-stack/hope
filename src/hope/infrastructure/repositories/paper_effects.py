from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.paper.effects import PaperEffect, PaperEffectType


class SqlAlchemyPaperEffectRepository:
    """Durable idempotency ledger for PAPER trading side effects."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._paper_effects = Table(
            "paper_effects",
            metadata,
            Column("effect_id", Uuid, primary_key=True),
            Column("job_run_id", Uuid, nullable=False),
            Column("effect_type", String, nullable=False),
            Column("entity_id", Uuid, nullable=False),
            Column("payload_hash", String(64), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def record(self, effect: PaperEffect) -> bool:
        """Record one logical effect; return False for an exact prior effect."""
        statement = (
            pg_insert(self._paper_effects)
            .values(
                effect_id=effect.effect_id,
                job_run_id=effect.job_run_id,
                effect_type=effect.effect_type.value,
                entity_id=effect.entity_id,
                payload_hash=effect.payload_hash,
            )
            .on_conflict_do_nothing(index_elements=["effect_type", "entity_id"])
            .returning(self._paper_effects.c.effect_id)
        )
        inserted_id = self._connection.execute(statement).scalar_one_or_none()
        if inserted_id is not None:
            return True

        existing = self._connection.execute(
            select(
                self._paper_effects.c.effect_id,
                self._paper_effects.c.payload_hash,
            ).where(
                self._paper_effects.c.effect_type == effect.effect_type.value,
                self._paper_effects.c.entity_id == effect.entity_id,
            )
        ).mappings().one()
        if existing["effect_id"] != effect.effect_id or existing["payload_hash"] != effect.payload_hash:
            raise ValueError("PAPER_EFFECT_IDENTITY_CONFLICT")
        return False

    def get(
        self,
        effect_type: PaperEffectType,
        entity_id: UUID,
    ) -> PaperEffect | None:
        parsed_type = PaperEffectType(effect_type)
        row = self._connection.execute(
            select(self._paper_effects).where(
                self._paper_effects.c.effect_type == parsed_type.value,
                self._paper_effects.c.entity_id == entity_id,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return PaperEffect(
            effect_id=row["effect_id"],
            job_run_id=row["job_run_id"],
            effect_type=parsed_type,
            entity_id=row["entity_id"],
            payload_hash=row["payload_hash"],
        )
