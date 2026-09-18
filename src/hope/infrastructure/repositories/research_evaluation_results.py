from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CHAR, JSON, Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.experiments.evaluation_results import (
    ResearchEvaluationResultDefinition,
    research_evaluation_result_fingerprint,
)


class ResearchEvaluationResultRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    variant_experiment_id: str
    control_run_id: UUID
    variant_run_id: UUID
    stage: str
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_result: dict
    created_at: datetime | None = None


class SqlAlchemyResearchEvaluationResultRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._results = Table(
            "research_evaluation_results",
            metadata,
            Column("variant_experiment_id", String, primary_key=True),
            Column("control_run_id", Uuid, primary_key=True),
            Column("variant_run_id", Uuid, primary_key=True),
            Column("stage", String, primary_key=True),
            Column("protocol_hash", CHAR(64), nullable=False),
            Column("result_fingerprint", CHAR(64), nullable=False),
            Column("canonical_result", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    def list_by_run_pair(
        self,
        variant_experiment_id: str,
        control_run_id: UUID,
        variant_run_id: UUID,
    ) -> list[ResearchEvaluationResultRecord]:
        rows = self._connection.execute(
            select(self._results)
            .where(
                self._results.c.variant_experiment_id == variant_experiment_id,
                self._results.c.control_run_id == control_run_id,
                self._results.c.variant_run_id == variant_run_id,
            )
            .order_by(self._results.c.stage)
        ).mappings().all()
        return [ResearchEvaluationResultRecord(**row) for row in rows]

    def persist(self, definition: ResearchEvaluationResultDefinition) -> bool:
        expected = research_evaluation_result_fingerprint(definition.canonical_result)
        if definition.result_fingerprint != expected:
            raise ValueError("RESEARCH_EVALUATION_RESULT_FINGERPRINT_MISMATCH")
        values = definition.model_dump()
        statement = (
            pg_insert(self._results)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    "variant_experiment_id",
                    "control_run_id",
                    "variant_run_id",
                    "stage",
                ]
            )
            .returning(self._results.c.stage)
        )
        inserted = self._connection.execute(statement).scalar_one_or_none()
        if inserted is not None:
            return True
        row = self._connection.execute(
            select(self._results).where(
                self._results.c.variant_experiment_id == definition.variant_experiment_id,
                self._results.c.control_run_id == definition.control_run_id,
                self._results.c.variant_run_id == definition.variant_run_id,
                self._results.c.stage == definition.stage,
            )
        ).mappings().one()
        if (
            row["protocol_hash"] != definition.protocol_hash
            or row["result_fingerprint"] != definition.result_fingerprint
            or row["canonical_result"] != definition.canonical_result
        ):
            raise ValueError("RESEARCH_EVALUATION_RESULT_CONFLICT")
        return False
