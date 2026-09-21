from __future__ import annotations

from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import CHAR, Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


class ResearchRunRecord(BaseModel):
    """Immutable scientific identity for one authoritative research execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    research_run_id: UUID
    experiment_id: str
    run_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: datetime
    created_at: datetime | None = None

    @field_validator("experiment_id")
    @classmethod
    def _canonical_experiment_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RESEARCH_RUN_EXPERIMENT_ID_REQUIRED")
        if value != value.strip():
            raise ValueError("RESEARCH_RUN_EXPERIMENT_ID_NOT_CANONICAL")
        return value

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("RESEARCH_RUN_AS_OF_MUST_BE_TIMEZONE_AWARE")
        return value


class SqlAlchemyResearchRunRepository:
    """Append-only, restart-safe research-run identity repository."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._runs = Table(
            "research_runs",
            metadata,
            Column("research_run_id", Uuid, primary_key=True),
            Column("experiment_id", String, nullable=False),
            Column("run_fingerprint", CHAR(64), nullable=False),
            Column("as_of", DateTime(timezone=True), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    @staticmethod
    def deterministic_id(experiment_id: str, run_fingerprint: str) -> UUID:
        """Stable identity lets a retry after process loss reconstruct the same run."""
        return uuid5(NAMESPACE_URL, f"hope:research-run:{experiment_id}:{run_fingerprint}")

    def claim(self, run: ResearchRunRecord) -> bool:
        expected_id = self.deterministic_id(run.experiment_id, run.run_fingerprint)
        if run.research_run_id != expected_id:
            raise ValueError("RESEARCH_RUN_ID_NOT_DETERMINISTIC")
        values = run.model_dump(exclude={"created_at"})
        statement = (
            pg_insert(self._runs)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["experiment_id", "run_fingerprint"])
            .returning(self._runs.c.research_run_id)
        )
        inserted = self._connection.execute(statement).scalar_one_or_none()
        if inserted is not None:
            return True

        existing = self.get_by_fingerprint(run.experiment_id, run.run_fingerprint)
        if existing is None:
            raise RuntimeError("RESEARCH_RUN_CLAIM_LOST")
        if existing.research_run_id != run.research_run_id or existing.as_of != run.as_of:
            raise ValueError("RESEARCH_RUN_IDENTITY_CONFLICT")
        return False

    @classmethod
    def _validate_stored_identity(cls, run: ResearchRunRecord) -> ResearchRunRecord:
        expected_id = cls.deterministic_id(run.experiment_id, run.run_fingerprint)
        if run.research_run_id != expected_id:
            raise ValueError("RESEARCH_RUN_STORED_ID_NOT_DETERMINISTIC")
        return run

    def get(self, research_run_id: UUID) -> ResearchRunRecord | None:
        row = self._connection.execute(
            select(self._runs).where(self._runs.c.research_run_id == research_run_id)
        ).mappings().one_or_none()
        if row is None:
            return None
        return self._validate_stored_identity(ResearchRunRecord(**row))

    def get_by_fingerprint(self, experiment_id: str, run_fingerprint: str) -> ResearchRunRecord | None:
        row = self._connection.execute(
            select(self._runs).where(
                self._runs.c.experiment_id == experiment_id,
                self._runs.c.run_fingerprint == run_fingerprint,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return self._validate_stored_identity(ResearchRunRecord(**row))
