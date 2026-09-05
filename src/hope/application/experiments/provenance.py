from datetime import datetime, timezone
from typing import Any

from hope.application.experiments.config_hash import configuration_hash
from hope.domain.provenance.models import ConfigurationSnapshot, ProvenanceRecord


def build_configuration_snapshot(configuration: Any, *, created_at: datetime | None = None) -> ConfigurationSnapshot:
    return ConfigurationSnapshot(
        configuration_hash=configuration_hash(configuration),
        created_at=created_at or datetime.now(timezone.utc),
    )


def build_provenance(
    *,
    dataset_version: str,
    universe_version: str,
    strategy_version: str,
    parameters: Any,
    cost_model_version: str,
    execution_model_version: str,
    code_commit: str,
    configuration: Any,
    environment,
    created_at: datetime | None = None,
) -> ProvenanceRecord:
    return ProvenanceRecord(
        dataset_version=dataset_version,
        universe_version=universe_version,
        strategy_version=strategy_version,
        parameter_snapshot_hash=configuration_hash(parameters),
        cost_model_version=cost_model_version,
        execution_model_version=execution_model_version,
        code_commit=code_commit,
        configuration_hash=configuration_hash(configuration),
        environment=environment,
        created_at=created_at or datetime.now(timezone.utc),
    )
