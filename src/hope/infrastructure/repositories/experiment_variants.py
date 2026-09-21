from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, Connection, DateTime, MetaData, String, Table, insert, select

from hope.domain.research.models import ExperimentVariantDefinition


class ExperimentVariantRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    control_experiment_id: str
    variant_experiment_id: str
    variant_label: str
    predeclared_at: datetime


class ExperimentVariantRepository(Protocol):
    def create(self, definition: ExperimentVariantDefinition) -> None: ...
    def get(self, variant_experiment_id: str) -> ExperimentVariantRecord | None: ...


class SqlAlchemyExperimentVariantRepository:
    """Durable predeclared experiment comparison registry."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._variants = Table(
            "experiment_variants",
            metadata,
            Column("variant_experiment_id", String, primary_key=True),
            Column("control_experiment_id", String, nullable=False),
            Column("variant_label", String, nullable=False),
            Column("predeclared_at", DateTime(timezone=True), nullable=False),
        )

    def create(self, definition: ExperimentVariantDefinition) -> None:
        if not isinstance(definition, ExperimentVariantDefinition):
            raise TypeError("EXPERIMENT_VARIANT_REPOSITORY_REQUIRES_DEFINITION")
        self._connection.execute(
            insert(self._variants).values(
                variant_experiment_id=definition.variant_experiment_id,
                control_experiment_id=definition.control_experiment_id,
                variant_label=definition.variant_label,
            )
        )

    def get(self, variant_experiment_id: str) -> ExperimentVariantRecord | None:
        row = self._connection.execute(
            select(self._variants).where(
                self._variants.c.variant_experiment_id == variant_experiment_id
            )
        ).mappings().one_or_none()
        if row is None:
            return None

        record = ExperimentVariantRecord(**row)
        ExperimentVariantDefinition(
            control_experiment_id=record.control_experiment_id,
            variant_experiment_id=record.variant_experiment_id,
            variant_label=record.variant_label,
        )
        return record
