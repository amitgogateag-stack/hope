from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, insert, select

from hope.domain.research.models import ResearchDecision, ResearchDecisionDefinition


class ResearchDecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    variant_experiment_id: str
    control_run_id: UUID
    variant_run_id: UUID
    comparison_id: UUID
    comparison_fingerprint: str
    decision: ResearchDecision
    rationale: str
    created_at: datetime


class ResearchDecisionRepository(Protocol):
    def create(self, definition: ResearchDecisionDefinition) -> None: ...
    def get(self, decision_id: str) -> ResearchDecisionRecord | None: ...


class SqlAlchemyResearchDecisionRepository:
    """Append-only evidence-backed research decision ledger."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._decisions = Table(
            "research_decisions",
            metadata,
            Column("decision_id", String, primary_key=True),
            Column("variant_experiment_id", String, nullable=False),
            Column("control_run_id", Uuid, nullable=False),
            Column("variant_run_id", Uuid, nullable=False),
            Column("comparison_id", Uuid, nullable=True),
            Column("comparison_fingerprint", String, nullable=True),
            Column("decision", String, nullable=False),
            Column("rationale", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def create(self, definition: ResearchDecisionDefinition) -> None:
        if not isinstance(definition, ResearchDecisionDefinition):
            raise TypeError("RESEARCH_DECISION_REPOSITORY_REQUIRES_DEFINITION")
        self._connection.execute(
            insert(self._decisions).values(
                decision_id=definition.decision_id,
                variant_experiment_id=definition.variant_experiment_id,
                control_run_id=definition.control_run_id,
                variant_run_id=definition.variant_run_id,
                comparison_id=definition.comparison_id,
                comparison_fingerprint=definition.comparison_fingerprint,
                decision=definition.decision.value,
                rationale=definition.rationale,
            )
        )

    def get(self, decision_id: str) -> ResearchDecisionRecord | None:
        row = self._connection.execute(
            select(self._decisions).where(self._decisions.c.decision_id == decision_id)
        ).mappings().one_or_none()
        return ResearchDecisionRecord(**row) if row else None
