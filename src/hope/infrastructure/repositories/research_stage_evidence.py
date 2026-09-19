from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import CHAR, JSON, Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.evaluation_protocol import REQUIRED_EVALUATION_STAGES


GENERIC_STAGE_EVIDENCE_STAGES = tuple(
    stage
    for stage in REQUIRED_EVALUATION_STAGES
    if stage not in {"regression_invariants", "historical_evaluation"}
)


def research_stage_evidence_fingerprint(canonical_result: dict) -> str:
    if not isinstance(canonical_result, dict) or not canonical_result:
        raise ValueError("RESEARCH_STAGE_EVIDENCE_RESULT_REQUIRED")
    return configuration_hash(canonical_result)


class ResearchStageEvidenceRecord(BaseModel):
    """Immutable stage-specific evidence bound to one authoritative research run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    research_run_id: UUID
    stage: str
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_result: dict
    created_at: datetime | None = None

    @field_validator("stage")
    @classmethod
    def validate_stage(cls, value: str) -> str:
        if value not in GENERIC_STAGE_EVIDENCE_STAGES:
            raise ValueError("RESEARCH_STAGE_EVIDENCE_STAGE_INVALID")
        return value


class SqlAlchemyResearchStageEvidenceRepository:
    """Persist one immutable evidence artifact per research run and evaluation stage."""

    def __init__(self, connection: Connection, stage: str) -> None:
        if stage not in GENERIC_STAGE_EVIDENCE_STAGES:
            raise ValueError("RESEARCH_STAGE_EVIDENCE_STAGE_INVALID")
        self._connection = connection
        self._stage = stage
        metadata = MetaData()
        self._evidence = Table(
            "research_stage_evidence",
            metadata,
            Column("research_run_id", Uuid, primary_key=True),
            Column("stage", String, primary_key=True),
            Column("result_fingerprint", CHAR(64), nullable=False),
            Column("canonical_result", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    @property
    def stage(self) -> str:
        return self._stage

    def persist(self, evidence: ResearchStageEvidenceRecord) -> bool:
        if evidence.stage != self._stage:
            raise ValueError("RESEARCH_STAGE_EVIDENCE_REPOSITORY_STAGE_MISMATCH")
        expected = research_stage_evidence_fingerprint(evidence.canonical_result)
        if evidence.result_fingerprint != expected:
            raise ValueError("RESEARCH_STAGE_EVIDENCE_FINGERPRINT_MISMATCH")

        values = evidence.model_dump(exclude={"created_at"})
        statement = (
            pg_insert(self._evidence)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["research_run_id", "stage"])
            .returning(self._evidence.c.research_run_id)
        )
        inserted = self._connection.execute(statement).scalar_one_or_none()
        if inserted is not None:
            return True

        existing = self.get(evidence.research_run_id)
        if existing is None:
            raise RuntimeError("RESEARCH_STAGE_EVIDENCE_PERSIST_LOST")
        if (
            existing.result_fingerprint != evidence.result_fingerprint
            or existing.canonical_result != evidence.canonical_result
        ):
            raise ValueError("RESEARCH_STAGE_EVIDENCE_CONFLICT")
        return False

    def get(self, research_run_id: UUID) -> ResearchStageEvidenceRecord | None:
        row = self._connection.execute(
            select(self._evidence).where(
                self._evidence.c.research_run_id == research_run_id,
                self._evidence.c.stage == self._stage,
            )
        ).mappings().one_or_none()
        return ResearchStageEvidenceRecord(**row) if row else None


def build_research_stage_evidence_repositories(
    connection: Connection,
    *,
    invariant_repository,
) -> dict[str, object]:
    """Build the canonical durable evidence-source map for the evaluator orchestrator."""
    if invariant_repository is None:
        raise ValueError("RESEARCH_INVARIANT_EVIDENCE_REPOSITORY_REQUIRED")
    repositories: dict[str, object] = {
        "regression_invariants": invariant_repository,
    }
    repositories.update(
        {
            stage: SqlAlchemyResearchStageEvidenceRepository(connection, stage)
            for stage in GENERIC_STAGE_EVIDENCE_STAGES
        }
    )
    return repositories
