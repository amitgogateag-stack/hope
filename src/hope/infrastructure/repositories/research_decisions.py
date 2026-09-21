from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, Uuid, insert, select

from hope.domain.research.models import ResearchDecision, ResearchDecisionDefinition
from hope.infrastructure.repositories.research_comparisons import (
    SqlAlchemyResearchComparisonRepository,
)


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
        if row is None:
            return None

        decision = ResearchDecisionRecord(**row)
        comparison = SqlAlchemyResearchComparisonRepository(
            self._connection
        ).get_by_run_pair(
            decision.variant_experiment_id,
            decision.control_run_id,
            decision.variant_run_id,
        )
        if comparison is None:
            raise ValueError("RESEARCH_DECISION_STORED_COMPARISON_MISSING")
        if decision.comparison_id != comparison.comparison_id:
            raise ValueError("RESEARCH_DECISION_STORED_COMPARISON_ID_MISMATCH")
        if decision.comparison_fingerprint != comparison.comparison_fingerprint:
            raise ValueError("RESEARCH_DECISION_STORED_COMPARISON_FINGERPRINT_MISMATCH")
        return decision
