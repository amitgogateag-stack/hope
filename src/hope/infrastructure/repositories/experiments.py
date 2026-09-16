from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import CHAR, Column, Connection, DateTime, MetaData, String, Table, Uuid, insert, select
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExperimentRecord(BaseModel):
    """Persistence-shaped immutable experiment definition plus derived invalidation state."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    experiment_id: str
    hypothesis: str
    strategy_version_id: UUID
    dataset_version_id: UUID
    universe_version_id: UUID
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment: Literal["RESEARCH", "BACKTEST", "WALK_FORWARD", "PAPER"]
    status: Literal["CREATED"]
    created_at: datetime | None = None
    invalidated_at: datetime | None = None

    @field_validator("experiment_id")
    @classmethod
    def _require_canonical_experiment_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("EXPERIMENT_ID_REQUIRED")
        if value != value.strip():
            raise ValueError("EXPERIMENT_ID_NOT_CANONICAL")
        return value

    @field_validator("hypothesis")
    @classmethod
    def _require_canonical_hypothesis(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("EXPERIMENT_HYPOTHESIS_REQUIRED")
        if value != value.strip():
            raise ValueError("EXPERIMENT_HYPOTHESIS_NOT_CANONICAL")
        return value


class ExperimentRepository(Protocol):
    def create(self, experiment: ExperimentRecord) -> None: ...
    def get(self, experiment_id: str) -> ExperimentRecord | None: ...
    def invalidate(self, experiment_id: str, reason: str) -> None: ...


class SqlAlchemyExperimentRepository:
    """Append-only experiment repository backed by the HOPE SQL schema.

    The experiment definition is immutable. Invalidation is recorded in the
    append-only experiment_invalidations table and never mutates the original.
    Repository reads project the invalidation timestamp so application services
    can fail closed even when replaying an already-existing research run.
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
        values = experiment.model_dump(exclude={"invalidated_at"})
        values["created_at"] = datetime.now(timezone.utc)
        self._connection.execute(insert(self._experiments).values(**values))

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        row = self._connection.execute(
            select(self._experiments, self._invalidations.c.invalidated_at)
            .select_from(
                self._experiments.outerjoin(
                    self._invalidations,
                    self._invalidations.c.experiment_id == self._experiments.c.experiment_id,
                )
            )
            .where(self._experiments.c.experiment_id == experiment_id)
        ).mappings().one_or_none()
        return ExperimentRecord(**row) if row else None

    def invalidate(self, experiment_id: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("EXPERIMENT_INVALIDATION_REASON_REQUIRED")
        if reason != reason.strip():
            raise ValueError("EXPERIMENT_INVALIDATION_REASON_NOT_CANONICAL")
        if self.get(experiment_id) is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        self._connection.execute(
            insert(self._invalidations).values(
                experiment_id=experiment_id,
                reason=reason,
                invalidated_at=datetime.now(timezone.utc),
            )
        )
