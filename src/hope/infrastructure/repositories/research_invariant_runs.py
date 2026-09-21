from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CHAR, JSON, Column, Connection, DateTime, MetaData, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.validation.research_invariants import (
    ResearchInvariantRunArtifact,
    _fingerprint,
)


class ResearchInvariantRunRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    research_run_id: UUID
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_results: list[dict[str, str]]
    created_at: datetime | None = None


class SqlAlchemyResearchInvariantRunRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._runs = Table(
            "research_invariant_runs",
            metadata,
            Column("research_run_id", Uuid, primary_key=True),
            Column("context_fingerprint", CHAR(64), nullable=False),
            Column("result_fingerprint", CHAR(64), nullable=False),
            Column("canonical_results", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def persist(self, artifact: ResearchInvariantRunArtifact) -> bool:
        if artifact.result_fingerprint != _fingerprint(artifact.canonical_results):
            raise ValueError("RESEARCH_INVARIANT_RESULT_FINGERPRINT_MISMATCH")
        statement = (
            pg_insert(self._runs)
            .values(**artifact.model_dump())
            .on_conflict_do_nothing(index_elements=["research_run_id"])
            .returning(self._runs.c.research_run_id)
        )
        inserted = self._connection.execute(statement).scalar_one_or_none()
        if inserted is not None:
            return True

        existing = self.get(artifact.research_run_id)
        if existing is None:
            raise RuntimeError("RESEARCH_INVARIANT_RUN_PERSIST_LOST")
        if (
            existing.context_fingerprint != artifact.context_fingerprint
            or existing.result_fingerprint != artifact.result_fingerprint
            or existing.canonical_results != artifact.canonical_results
        ):
            raise ValueError("RESEARCH_INVARIANT_RUN_CONFLICT")
        return False

    def get(self, research_run_id: UUID) -> ResearchInvariantRunRecord | None:
        row = self._connection.execute(
            select(self._runs).where(self._runs.c.research_run_id == research_run_id)
        ).mappings().one_or_none()
        if row is None:
            return None

        record = ResearchInvariantRunRecord(**row)
        if record.result_fingerprint != _fingerprint(record.canonical_results):
            raise ValueError("RESEARCH_INVARIANT_STORED_RESULT_FINGERPRINT_MISMATCH")
        return record
