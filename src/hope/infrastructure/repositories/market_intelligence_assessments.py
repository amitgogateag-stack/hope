from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.domain.market_intelligence.assessment import (
    IntelligenceAssessment,
    IntelligenceDisposition,
)
from hope.domain.market_intelligence.models import IntelligenceAction


class IntelligenceAssessmentRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    assessment_id: UUID
    event_id: UUID
    policy_version: str
    disposition: IntelligenceDisposition
    source_action: IntelligenceAction
    created_at: datetime


class SqlAlchemyIntelligenceAssessmentRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._assessments = Table(
            "market_intelligence_assessments",
            metadata,
            Column("assessment_id", Uuid, primary_key=True),
            Column("event_id", Uuid, nullable=False),
            Column("policy_version", String, nullable=False),
            Column("disposition", String, nullable=False),
            Column("source_action", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def persist(self, assessment_id: UUID, assessment: IntelligenceAssessment) -> bool:
        statement = (
            pg_insert(self._assessments)
            .values(
                assessment_id=assessment_id,
                event_id=assessment.event_id,
                policy_version=assessment.policy_version,
                disposition=assessment.disposition.value,
                source_action=assessment.source_action.value,
            )
            .on_conflict_do_nothing(index_elements=["event_id", "policy_version"])
            .returning(self._assessments.c.assessment_id)
        )
        return self._connection.execute(statement).scalar_one_or_none() is not None

    def get_for_event(
        self,
        event_id: UUID,
        *,
        policy_version: str,
    ) -> IntelligenceAssessmentRecord | None:
        row = self._connection.execute(
            select(self._assessments).where(
                self._assessments.c.event_id == event_id,
                self._assessments.c.policy_version == policy_version,
            )
        ).mappings().one_or_none()
        return IntelligenceAssessmentRecord(**row) if row else None
