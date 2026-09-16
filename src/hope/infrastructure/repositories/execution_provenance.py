from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import CHAR, JSON, Column, Connection, MetaData, String, Table, Uuid, select

from hope.application.experiments.config_hash import configuration_hash
from hope.infrastructure.repositories.experiments import ExperimentRecord


@dataclass(frozen=True)
class StrategyVersionRecord:
    strategy_version_id: UUID
    strategy_id: UUID
    version: str
    code_commit: str


@dataclass(frozen=True)
class CertifiedExecutionPlan:
    experiment_id: str
    strategy_version_id: UUID
    strategy_id: UUID
    strategy_version: str
    code_commit: str
    configuration_hash: str
    configuration: Any


class ExecutionProvenanceRepository(Protocol):
    def get_strategy_version(self, strategy_version_id: UUID) -> StrategyVersionRecord | None: ...
    def get_configuration(self, configuration_hash: str) -> Any | None: ...


class SqlAlchemyExecutionProvenanceRepository:
    """Read immutable strategy/configuration provenance used by certified research."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._strategy_versions = Table(
            "strategy_versions",
            metadata,
            Column("strategy_version_id", Uuid, primary_key=True),
            Column("strategy_id", Uuid, nullable=False),
            Column("version", String, nullable=False),
            Column("code_commit", String, nullable=False),
        )
        self._configuration_snapshots = Table(
            "configuration_snapshots",
            metadata,
            Column("configuration_hash", CHAR(64), primary_key=True),
            Column("canonical_json", JSON, nullable=False),
        )

    def get_strategy_version(self, strategy_version_id: UUID) -> StrategyVersionRecord | None:
        row = self._connection.execute(
            select(self._strategy_versions).where(
                self._strategy_versions.c.strategy_version_id == strategy_version_id
            )
        ).mappings().one_or_none()
        return StrategyVersionRecord(**row) if row else None

    def get_configuration(self, configuration_hash_value: str) -> Any | None:
        return self._connection.execute(
            select(self._configuration_snapshots.c.canonical_json).where(
                self._configuration_snapshots.c.configuration_hash == configuration_hash_value
            )
        ).scalar_one_or_none()


class CertifiedExecutionPlanResolver:
    """Resolve and independently verify immutable execution provenance for an experiment."""

    def __init__(self, provenance_repository: ExecutionProvenanceRepository) -> None:
        self._provenance = provenance_repository

    def resolve(self, experiment: ExperimentRecord) -> CertifiedExecutionPlan:
        strategy = self._provenance.get_strategy_version(experiment.strategy_version_id)
        if strategy is None:
            raise ValueError("RESEARCH_EXECUTION_STRATEGY_VERSION_MISSING")
        if strategy.strategy_version_id != experiment.strategy_version_id:
            raise ValueError("RESEARCH_EXECUTION_STRATEGY_VERSION_MISMATCH")
        if not strategy.version.strip() or strategy.version != strategy.version.strip():
            raise ValueError("RESEARCH_EXECUTION_STRATEGY_VERSION_NOT_CANONICAL")
        if not strategy.code_commit.strip() or strategy.code_commit != strategy.code_commit.strip():
            raise ValueError("RESEARCH_EXECUTION_CODE_COMMIT_NOT_CANONICAL")

        configuration = self._provenance.get_configuration(experiment.configuration_hash)
        if configuration is None:
            raise ValueError("RESEARCH_EXECUTION_CONFIGURATION_MISSING")
        if configuration_hash(configuration) != experiment.configuration_hash:
            raise ValueError("RESEARCH_EXECUTION_CONFIGURATION_HASH_MISMATCH")

        return CertifiedExecutionPlan(
            experiment_id=experiment.experiment_id,
            strategy_version_id=strategy.strategy_version_id,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            code_commit=strategy.code_commit,
            configuration_hash=experiment.configuration_hash,
            configuration=configuration,
        )
