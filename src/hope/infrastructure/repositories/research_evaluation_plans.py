from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CHAR, JSON, Column, Connection, DateTime, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.experiments.evaluation_protocol import (
    ResearchEvaluationPlanDefinition,
    research_evaluation_plan_hash,
)


class ResearchEvaluationPlanRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    variant_experiment_id: str
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_protocol: dict[str, Any]
    predeclared_at: datetime | None = None


class SqlAlchemyResearchEvaluationPlanRepository:
    """Persist one immutable evaluation protocol per predeclared variant."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._plans = Table(
            "research_evaluation_plans",
            metadata,
            Column("variant_experiment_id", String, primary_key=True),
            Column("protocol_hash", CHAR(64), nullable=False),
            Column("canonical_protocol", JSON, nullable=False),
            Column("predeclared_at", DateTime(timezone=True), nullable=False),
        )

    def persist(self, definition: ResearchEvaluationPlanDefinition) -> bool:
        protocol_hash = research_evaluation_plan_hash(definition.protocol)
        statement = (
            pg_insert(self._plans)
            .values(
                variant_experiment_id=definition.variant_experiment_id,
                protocol_hash=protocol_hash,
                canonical_protocol=definition.protocol,
            )
            .on_conflict_do_nothing(index_elements=["variant_experiment_id"])
            .returning(self._plans.c.variant_experiment_id)
        )
        inserted = self._connection.execute(statement).scalar_one_or_none()
        if inserted is not None:
            return True

        existing = self.get(definition.variant_experiment_id)
        if existing is None:
            raise RuntimeError("RESEARCH_EVALUATION_PLAN_PERSIST_LOST")
        if (
            existing.protocol_hash != protocol_hash
            or existing.canonical_protocol != definition.protocol
        ):
            raise ValueError("RESEARCH_EVALUATION_PLAN_CONFLICT")
        return False

    def get(self, variant_experiment_id: str) -> ResearchEvaluationPlanRecord | None:
        row = self._connection.execute(
            select(self._plans).where(
                self._plans.c.variant_experiment_id == variant_experiment_id
            )
        ).mappings().one_or_none()
        return ResearchEvaluationPlanRecord(**row) if row else None
