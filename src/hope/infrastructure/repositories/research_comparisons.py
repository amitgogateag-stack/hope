from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CHAR, JSON, Column, Connection, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.application.experiments.comparisons import research_comparison_fingerprint


class ResearchComparisonRecord(BaseModel):
    """Immutable artifact representing the exact control-vs-variant comparison."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    comparison_id: UUID
    variant_experiment_id: str
    control_run_id: UUID
    variant_run_id: UUID
    control_result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant_result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparison_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_comparison: Any
    created_at: datetime | None = None


class SqlAlchemyResearchComparisonRepository:
    """Persist one canonical immutable comparison for an exact evidence pair."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._comparisons = Table(
            "research_comparisons",
            metadata,
            Column("comparison_id", Uuid, primary_key=True),
            Column("variant_experiment_id", String, nullable=False),
            Column("control_run_id", Uuid, nullable=False),
            Column("variant_run_id", Uuid, nullable=False),
            Column("control_result_fingerprint", CHAR(64), nullable=False),
            Column("variant_result_fingerprint", CHAR(64), nullable=False),
            Column("comparison_fingerprint", CHAR(64), nullable=False),
            Column("canonical_comparison", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )

    @staticmethod
    def deterministic_id(
        *,
        variant_experiment_id: str,
        control_run_id: UUID,
        variant_run_id: UUID,
        control_result_fingerprint: str,
        variant_result_fingerprint: str,
        comparison_fingerprint: str,
    ) -> UUID:
        identity = ":".join(
            (
                "hope:research-comparison",
                variant_experiment_id,
                str(control_run_id),
                str(variant_run_id),
                control_result_fingerprint,
                variant_result_fingerprint,
                comparison_fingerprint,
            )
        )
        return uuid5(NAMESPACE_URL, identity)

    def persist(self, comparison: ResearchComparisonRecord) -> bool:
        expected_fingerprint = research_comparison_fingerprint(
            comparison.canonical_comparison
        )
        if comparison.comparison_fingerprint != expected_fingerprint:
            raise ValueError("RESEARCH_COMPARISON_FINGERPRINT_MISMATCH")

        expected_id = self.deterministic_id(
            variant_experiment_id=comparison.variant_experiment_id,
            control_run_id=comparison.control_run_id,
            variant_run_id=comparison.variant_run_id,
            control_result_fingerprint=comparison.control_result_fingerprint,
            variant_result_fingerprint=comparison.variant_result_fingerprint,
            comparison_fingerprint=comparison.comparison_fingerprint,
        )
        if comparison.comparison_id != expected_id:
            raise ValueError("RESEARCH_COMPARISON_ID_NOT_DETERMINISTIC")

        values = comparison.model_dump(exclude={"created_at"})
        statement = (
            pg_insert(self._comparisons)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    "variant_experiment_id",
                    "control_run_id",
                    "variant_run_id",
                ]
            )
            .returning(self._comparisons.c.comparison_id)
        )
        inserted = self._connection.execute(statement).scalar_one_or_none()
        if inserted is not None:
            return True

        existing = self.get_by_run_pair(
            comparison.variant_experiment_id,
            comparison.control_run_id,
            comparison.variant_run_id,
        )
        if existing is None:
            raise RuntimeError("RESEARCH_COMPARISON_PERSIST_LOST")
        if existing != comparison.model_copy(update={"created_at": existing.created_at}):
            raise ValueError("RESEARCH_COMPARISON_CONFLICT")
        return False

    def get_by_run_pair(
        self,
        variant_experiment_id: str,
        control_run_id: UUID,
        variant_run_id: UUID,
    ) -> ResearchComparisonRecord | None:
        row = self._connection.execute(
            select(self._comparisons).where(
                self._comparisons.c.variant_experiment_id == variant_experiment_id,
                self._comparisons.c.control_run_id == control_run_id,
                self._comparisons.c.variant_run_id == variant_run_id,
            )
        ).mappings().one_or_none()
        return ResearchComparisonRecord(**row) if row else None
