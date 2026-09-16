from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CHAR, JSON, Column, Connection, DateTime, MetaData, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


class ResearchRunEvidenceRecord(BaseModel):
    """Immutable canonical output evidence for an authoritative research run."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    research_run_id: UUID
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_result: Any
    created_at: datetime | None = None


class SqlAlchemyResearchRunEvidenceRepository:
    """Persist one immutable result per research run with restart-safe retries."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._evidence = Table(
            "research_run_evidence",
            metadata,
            Column("research_run_id", Uuid, primary_key=True),
            Column("result_fingerprint", CHAR(64), nullable=False),
            Column("canonical_result", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def persist(self, evidence: ResearchRunEvidenceRecord) -> bool:
        values = evidence.model_dump(exclude={"created_at"})
        statement = (
            pg_insert(self._evidence)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["research_run_id"])
            .returning(self._evidence.c.research_run_id)
        )
        inserted = self._connection.execute(statement).scalar_one_or_none()
        if inserted is not None:
            return True
        existing = self.get(evidence.research_run_id)
        if existing is None:
            raise RuntimeError("RESEARCH_RUN_EVIDENCE_PERSIST_LOST")
        if (
            existing.result_fingerprint != evidence.result_fingerprint
            or existing.canonical_result != evidence.canonical_result
        ):
            raise ValueError("RESEARCH_RUN_EVIDENCE_CONFLICT")
        return False

    def get(self, research_run_id: UUID) -> ResearchRunEvidenceRecord | None:
        row = self._connection.execute(
            select(self._evidence).where(self._evidence.c.research_run_id == research_run_id)
        ).mappings().one_or_none()
        return ResearchRunEvidenceRecord(**row) if row else None
