from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.domain.market_intelligence.review import (
    IntelligenceReviewOutcome,
    IntelligenceReviewResolution,
)


class IntelligenceReviewResolutionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resolution_id: UUID
    assessment_id: UUID
    policy_version: str
    outcome: IntelligenceReviewOutcome
    rationale: str
    created_at: datetime

    @field_validator("policy_version", "rationale")
    @classmethod
    def require_canonical_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("INTELLIGENCE_REVIEW_TEXT_REQUIRED")
        if value != value.strip():
            raise ValueError("INTELLIGENCE_REVIEW_TEXT_NOT_CANONICAL")
        return value


class SqlAlchemyIntelligenceReviewRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._resolutions = Table(
            "market_intelligence_review_resolutions",
            metadata,
            Column("resolution_id", Uuid, primary_key=True),
            Column("assessment_id", Uuid, nullable=False),
            Column("policy_version", String, nullable=False),
            Column("outcome", String, nullable=False),
            Column("rationale", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def resolve(
        self,
        resolution_id: UUID,
        resolution: IntelligenceReviewResolution,
    ) -> bool:
        statement = (
            pg_insert(self._resolutions)
            .values(
                resolution_id=resolution_id,
                assessment_id=resolution.assessment_id,
                policy_version=resolution.policy_version,
                outcome=resolution.outcome.value,
                rationale=resolution.rationale,
            )
            .on_conflict_do_nothing(
                index_elements=["assessment_id", "policy_version"]
            )
            .returning(self._resolutions.c.resolution_id)
        )
        return self._connection.execute(statement).scalar_one_or_none() is not None

    def get(
        self,
        assessment_id: UUID,
        *,
        policy_version: str,
    ) -> IntelligenceReviewResolutionRecord | None:
        row = self._connection.execute(
            select(self._resolutions).where(
                self._resolutions.c.assessment_id == assessment_id,
                self._resolutions.c.policy_version == policy_version,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        record = IntelligenceReviewResolutionRecord(**row)
        IntelligenceReviewResolution(
            assessment_id=record.assessment_id,
            policy_version=record.policy_version,
            outcome=record.outcome,
            rationale=record.rationale,
        )
        return record
