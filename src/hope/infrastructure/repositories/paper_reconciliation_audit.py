from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, and_, or_, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert

from hope.application.jobs import JobRunRecord, JobRunStatus
from hope.application.paper.effects import PaperEffect, PaperEffectType


_FILLED_EFFECT_TYPES = frozenset(
    {
        PaperEffectType.SIGNAL,
        PaperEffectType.RISK,
        PaperEffectType.ORDER,
        PaperEffectType.FILL,
        PaperEffectType.PNL,
    }
)
_NON_FILL_BASE_EFFECT_TYPES = frozenset(
    {
        PaperEffectType.SIGNAL,
        PaperEffectType.RISK,
        PaperEffectType.ORDER,
    }
)
_COMPLETE_EFFECT_SHAPES = frozenset(
    {
        _FILLED_EFFECT_TYPES,
        _NON_FILL_BASE_EFFECT_TYPES | {PaperEffectType.CANCELLATION},
        _NON_FILL_BASE_EFFECT_TYPES | {PaperEffectType.REJECTION},
    }
)


class SqlAlchemyPaperReconciliationAuditRepository:
    """Append and verify an immutable receipt for one no-replay PAPER reconciliation."""

    _EVENT_TYPE = "PAPER_RUN_RECONCILED"
    _ENTITY_TYPE = "JOB_RUN"

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._audit_events = Table(
            "audit_events",
            metadata,
            Column("audit_event_id", Uuid, primary_key=True),
            Column("event_type", String, nullable=False),
            Column("entity_type", String, nullable=False),
            Column("entity_id", String, nullable=False),
            Column("payload", JSONB, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    @staticmethod
    def _event_id(job_run_id: UUID) -> UUID:
        return uuid5(NAMESPACE_URL, f"hope:paper:reconciliation:{job_run_id}")

    def _expected(
        self,
        completion: JobRunRecord,
        effects: tuple[PaperEffect, ...],
    ) -> tuple[UUID, str, dict[str, object]]:
        if completion.status is not JobRunStatus.SUCCEEDED:
            raise ValueError("PAPER_RECONCILIATION_AUDIT_REQUIRES_SUCCESS")
        if completion.completed_at is None:
            raise ValueError("PAPER_RECONCILIATION_AUDIT_REQUIRES_COMPLETION_TIME")
        if any(effect.job_run_id != completion.run.job_run_id for effect in effects):
            raise ValueError("PAPER_RECONCILIATION_AUDIT_EFFECT_RUN_MISMATCH")
        effect_types = tuple(effect.effect_type for effect in effects)
        if (
            len(effect_types) != len(set(effect_types))
            or frozenset(effect_types) not in _COMPLETE_EFFECT_SHAPES
        ):
            raise ValueError("PAPER_RECONCILIATION_AUDIT_EFFECT_SHAPE_INVALID")

        entity_id = str(completion.run.job_run_id)
        payload: dict[str, object] = {
            "schema_version": 1,
            "decision": "ACKNOWLEDGE_COMPLETE_EFFECTS",
            "job_key": completion.run.job_key,
            "scheduled_for": completion.run.scheduled_for.isoformat(),
            "completed_at": completion.completed_at.isoformat(),
            "effects": [
                {
                    "effect_id": str(effect.effect_id),
                    "effect_type": effect.effect_type.value,
                    "entity_id": str(effect.entity_id),
                    "payload_hash": effect.payload_hash,
                }
                for effect in effects
            ],
        }
        return self._event_id(completion.run.job_run_id), entity_id, payload

    def verify(
        self,
        completion: JobRunRecord,
        effects: tuple[PaperEffect, ...],
    ) -> bool:
        """Return whether the exact durable reconciliation receipt already exists."""

        event_id, entity_id, payload = self._expected(completion, effects)
        matches = self._connection.execute(
            select(
                self._audit_events.c.audit_event_id,
                self._audit_events.c.event_type,
                self._audit_events.c.entity_type,
                self._audit_events.c.entity_id,
                self._audit_events.c.payload,
            ).where(
                or_(
                    self._audit_events.c.audit_event_id == event_id,
                    and_(
                        self._audit_events.c.event_type == self._EVENT_TYPE,
                        self._audit_events.c.entity_type == self._ENTITY_TYPE,
                        self._audit_events.c.entity_id == entity_id,
                    ),
                )
            )
        ).mappings().all()
        if not matches:
            return False
        if len(matches) != 1 or dict(matches[0]) != {
            "audit_event_id": event_id,
            "event_type": self._EVENT_TYPE,
            "entity_type": self._ENTITY_TYPE,
            "entity_id": entity_id,
            "payload": payload,
        }:
            raise ValueError("PAPER_RECONCILIATION_AUDIT_IDENTITY_CONFLICT")
        return True

    def record(
        self,
        completion: JobRunRecord,
        effects: tuple[PaperEffect, ...],
    ) -> bool:
        event_id, entity_id, payload = self._expected(completion, effects)
        statement = (
            pg_insert(self._audit_events)
            .values(
                audit_event_id=event_id,
                event_type=self._EVENT_TYPE,
                entity_type=self._ENTITY_TYPE,
                entity_id=entity_id,
                payload=payload,
            )
            .on_conflict_do_nothing()
            .returning(self._audit_events.c.audit_event_id)
        )
        inserted_id = self._connection.execute(statement).scalar_one_or_none()
        if inserted_id is not None:
            if not self.verify(completion, effects):
                raise RuntimeError("PAPER_RECONCILIATION_AUDIT_NOT_DURABLE")
            return True
        if not self.verify(completion, effects):
            raise RuntimeError("PAPER_RECONCILIATION_AUDIT_NOT_DURABLE")
        return False
