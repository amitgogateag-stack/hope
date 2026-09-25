from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.domain.market_intelligence.assessment import (
    IntelligenceAssessment,
    IntelligenceDisposition,
)
from hope.domain.market_intelligence.gate import IntelligenceEntryGateContext
from hope.domain.market_intelligence.models import IntelligenceAction


class IntelligenceAssessmentRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    assessment_id: UUID
    event_id: UUID
    policy_version: str
    disposition: IntelligenceDisposition
    source_action: IntelligenceAction
    created_at: datetime

    @field_validator("policy_version")
    @classmethod
    def require_canonical_policy_version(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("INTELLIGENCE_ASSESSMENT_POLICY_NOT_CANONICAL")
        return value


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
        self._events = Table(
            "market_intelligence_events",
            metadata,
            Column("event_id", Uuid, primary_key=True),
            Column("scope", String, nullable=False),
            Column("instrument_id", Uuid, nullable=True),
            Column("available_time", DateTime(timezone=True), nullable=False),
            Column("ingestion_time", DateTime(timezone=True), nullable=False),
        )
        self._entry_blocks = Table(
            "current_market_intelligence_entry_blocks",
            metadata,
            Column("assessment_id", Uuid, primary_key=True),
            Column("event_id", Uuid, nullable=False),
            Column("policy_version", String, nullable=False),
            Column("disposition", String, nullable=False),
            Column("source_action", String, nullable=False),
            Column("scope", String, nullable=False),
            Column("instrument_id", Uuid, nullable=True),
            Column("available_time", DateTime(timezone=True), nullable=False),
            Column("review_outcome", String, nullable=True),
        )
        self._resolutions = Table(
            "market_intelligence_review_resolutions",
            metadata,
            Column("resolution_id", Uuid, primary_key=True),
            Column("assessment_id", Uuid, nullable=False),
            Column("policy_version", String, nullable=False),
            Column("outcome", String, nullable=False),
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
        if row is None:
            return None
        record = IntelligenceAssessmentRecord(**row)
        IntelligenceAssessment(
            event_id=record.event_id,
            policy_version=record.policy_version,
            disposition=record.disposition,
            source_action=record.source_action,
        )
        return record


    def entry_gate_context(
        self,
        instrument_id: UUID,
        *,
        as_of: datetime,
        policy_version: str = "hope.intelligence-policy.v1",
    ) -> IntelligenceEntryGateContext:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("INTELLIGENCE_ENTRY_GATE_TIME_MUST_BE_TIMEZONE_AWARE")
        if not policy_version or policy_version != policy_version.strip():
            raise ValueError("INTELLIGENCE_ENTRY_GATE_POLICY_NOT_CANONICAL")

        assessment_resolution = self._assessments.outerjoin(
            self._resolutions,
            and_(
                self._resolutions.c.assessment_id == self._assessments.c.assessment_id,
                self._resolutions.c.policy_version == self._assessments.c.policy_version,
            ),
        )
        source = assessment_resolution.join(
            self._events,
            self._events.c.event_id == self._assessments.c.event_id,
        )
        rows = self._connection.execute(
            select(self._assessments.c.assessment_id)
            .select_from(source)
            .where(
                self._assessments.c.policy_version == policy_version,
                self._assessments.c.created_at <= as_of,
                self._events.c.available_time <= as_of,
                self._events.c.ingestion_time <= as_of,
                self._assessments.c.disposition.in_((
                    "ENTRY_ELIGIBILITY_REVIEW",
                    "MARKET_RISK_REVIEW",
                )),
                or_(
                    self._events.c.scope == "MARKET",
                    and_(
                        self._events.c.scope == "COMPANY",
                        self._events.c.instrument_id == instrument_id,
                    ),
                ),
                or_(
                    self._resolutions.c.resolution_id.is_(None),
                    self._resolutions.c.created_at > as_of,
                    self._resolutions.c.outcome == "BLOCK_CONFIRMED",
                ),
            )
            .order_by(self._assessments.c.assessment_id)
        ).scalars().all()
        return IntelligenceEntryGateContext(
            blocker_assessment_ids=tuple(rows)
        )
