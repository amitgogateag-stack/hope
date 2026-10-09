from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.jobs import JobRunStatus
from hope.application.paper.effects import PaperEffect, PaperEffectType


class SqlAlchemyPaperEffectRepository:
    """Durable idempotency ledger for PAPER trading side effects."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._job_runs = Table(
            "job_runs",
            metadata,
            Column("job_run_id", Uuid, primary_key=True),
            Column("status", String, nullable=False),
        )
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
        """Record one logical effect; return False for a safely reusable prior effect."""
        if not isinstance(effect, PaperEffect):
            raise ValueError("PAPER_EFFECT_RECORD_REQUIRES_EFFECT")
        # Defend the persistence boundary even against an object reconstructed
        # without invoking the frozen dataclass constructor.
        effect.__post_init__()
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
                self._paper_effects.c.job_run_id,
                self._paper_effects.c.payload_hash,
                self._job_runs.c.status.label("owner_status"),
            )
            .select_from(
                self._paper_effects.join(
                    self._job_runs,
                    self._job_runs.c.job_run_id == self._paper_effects.c.job_run_id,
                )
            )
            .where(
                self._paper_effects.c.effect_type == effect.effect_type.value,
                self._paper_effects.c.entity_id == effect.entity_id,
            )
        ).mappings().one_or_none()
        if existing is None:
            # A conflicting primary-key identity may have prevented insertion
            # even though no matching logical (type, entity) effect exists.
            raise ValueError("PAPER_EFFECT_IDENTITY_CONFLICT")
        if (
            existing["effect_id"] != effect.effect_id
            or existing["payload_hash"] != effect.payload_hash
            or (
                existing["job_run_id"] != effect.job_run_id
                and existing["owner_status"] != JobRunStatus.SUCCEEDED.value
            )
        ):
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
        return self._from_row(row)

    def get_reusable_for_job(
        self,
        effect_type: PaperEffectType,
        entity_id: UUID,
        job_run_id: UUID,
    ) -> PaperEffect | None:
        """Return an effect only when its owner may safely supply current work."""
        parsed_type = PaperEffectType(effect_type)
        row = self._connection.execute(
            select(
                self._paper_effects,
                self._job_runs.c.status.label("owner_status"),
            )
            .select_from(
                self._paper_effects.join(
                    self._job_runs,
                    self._job_runs.c.job_run_id == self._paper_effects.c.job_run_id,
                )
            )
            .where(
                self._paper_effects.c.effect_type == parsed_type.value,
                self._paper_effects.c.entity_id == entity_id,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        if (
            row["job_run_id"] != job_run_id
            and row["owner_status"] != JobRunStatus.SUCCEEDED.value
        ):
            raise ValueError("PAPER_EFFECT_IDENTITY_CONFLICT")
        return self._from_row(row)

    def get_recoverable(
        self,
        effect_type: PaperEffectType,
        entity_id: UUID,
    ) -> PaperEffect | None:
        """Return durable recovery evidence only when its owner completed successfully."""
        parsed_type = PaperEffectType(effect_type)
        row = self._connection.execute(
            select(
                self._paper_effects,
                self._job_runs.c.status.label("owner_status"),
            )
            .select_from(
                self._paper_effects.join(
                    self._job_runs,
                    self._job_runs.c.job_run_id == self._paper_effects.c.job_run_id,
                )
            )
            .where(
                self._paper_effects.c.effect_type == parsed_type.value,
                self._paper_effects.c.entity_id == entity_id,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        if row["owner_status"] != JobRunStatus.SUCCEEDED.value:
            raise ValueError("PAPER_EFFECT_OWNER_NOT_RECOVERABLE")
        return self._from_row(row)

    def list_for_job_run(self, job_run_id: UUID) -> tuple[PaperEffect, ...]:
        """Return all durable PAPER effects attributed to one scheduled run."""
        rows = self._connection.execute(
            select(self._paper_effects)
            .where(self._paper_effects.c.job_run_id == job_run_id)
            .order_by(self._paper_effects.c.created_at, self._paper_effects.c.effect_id)
        ).mappings().all()
        effects = tuple(self._from_row(row) for row in rows)
        if any(effect.job_run_id != job_run_id for effect in effects):
            raise ValueError("PAPER_EFFECT_JOB_HISTORY_OWNER_MISMATCH")
        if len({effect.effect_id for effect in effects}) != len(effects):
            raise ValueError("PAPER_EFFECT_JOB_HISTORY_DUPLICATE")
        if len({(effect.effect_type, effect.entity_id) for effect in effects}) != len(effects):
            raise ValueError("PAPER_EFFECT_JOB_HISTORY_DUPLICATE")
        return effects

    @staticmethod
    def _from_row(row) -> PaperEffect:
        return PaperEffect(
            effect_id=row["effect_id"],
            job_run_id=row["job_run_id"],
            effect_type=PaperEffectType(row["effect_type"]),
            entity_id=row["entity_id"],
            payload_hash=row["payload_hash"],
        )
