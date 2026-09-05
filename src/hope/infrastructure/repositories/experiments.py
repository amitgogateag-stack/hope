from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy import CHAR, Column, Connection, DateTime, MetaData, String, Table, Uuid, insert, select
from pydantic import BaseModel, ConfigDict, Field


class ExperimentRecord(BaseModel):
    """Persistence-shaped experiment record matching migrations/001_initial.sql."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    experiment_id: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    strategy_version_id: UUID
    dataset_version_id: UUID
    universe_version_id: UUID
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment: str = Field(min_length=1)
    status: str = Field(min_length=1)
    created_at: datetime | None = None


class ExperimentRepository(Protocol):
    def create(self, experiment: ExperimentRecord) -> None: ...
    def get(self, experiment_id: str) -> ExperimentRecord | None: ...
    def invalidate(self, experiment_id: str, reason: str) -> None: ...


class SqlAlchemyExperimentRepository:
    """Append-only experiment repository backed by the HOPE SQL schema.

    The experiment definition is immutable. Invalidation is recorded in the
    append-only experiment_invalidations table and never mutates the original.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._experiments = Table(
            "experiments",
            metadata,
            Column("experiment_id", String, primary_key=True),
            Column("hypothesis", String, nullable=False),
            Column("strategy_version_id", Uuid, nullable=False),
            Column("dataset_version_id", Uuid, nullable=False),
            Column("universe_version_id", Uuid, nullable=False),
            Column("configuration_hash", CHAR(64), nullable=False),
            Column("environment", String, nullable=False),
            Column("status", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self._invalidations = Table(
            "experiment_invalidations",
            metadata,
            Column("experiment_id", String, primary_key=True),
            Column("reason", String, nullable=False),
            Column("invalidated_at", DateTime(timezone=True), nullable=False),
        )
        self._metadata = metadata

    def create(self, experiment: ExperimentRecord) -> None:
        values = experiment.model_dump()
        values["created_at"] = datetime.now(timezone.utc)
        self._connection.execute(insert(self._experiments).values(**values))

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        row = self._connection.execute(
            select(self._experiments).where(self._experiments.c.experiment_id == experiment_id)
        ).mappings().one_or_none()
        return ExperimentRecord(**row) if row else None

    def invalidate(self, experiment_id: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("invalidation reason must not be empty")
        if self.get(experiment_id) is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        self._connection.execute(
            insert(self._invalidations).values(
                experiment_id=experiment_id,
                reason=reason,
                invalidated_at=datetime.now(timezone.utc),
            )
        )
