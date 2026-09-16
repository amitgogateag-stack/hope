from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from hope.infrastructure.repositories.experiments import ExperimentRecord


def research_run_fingerprint(
    experiment: ExperimentRecord,
    *,
    as_of: datetime,
    instrument_ids: tuple[UUID, ...],
) -> str:
    """Bind run identity to the immutable experiment and exact evidence request."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("RESEARCH_RUN_AS_OF_MUST_BE_TIMEZONE_AWARE")
    if not isinstance(instrument_ids, tuple) or any(not isinstance(value, UUID) for value in instrument_ids):
        raise TypeError("RESEARCH_RUN_REQUIRES_INSTRUMENT_IDS")
    if len(set(instrument_ids)) != len(instrument_ids):
        raise ValueError("RESEARCH_RUN_DUPLICATE_INSTRUMENT")

    payload = {
        "experiment_id": experiment.experiment_id,
        "strategy_version_id": str(experiment.strategy_version_id),
        "dataset_version_id": str(experiment.dataset_version_id),
        "universe_version_id": str(experiment.universe_version_id),
        "configuration_hash": experiment.configuration_hash,
        "environment": experiment.environment,
        "as_of": as_of.isoformat(),
        "instrument_ids": sorted(str(value) for value in instrument_ids),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CertifiedResearchContextLoader:
    """Resolve authoritative research market evidence only from experiment provenance."""

    def __init__(self, experiment_repository, market_context_repository) -> None:
        self._experiments = experiment_repository
        self._market_contexts = market_context_repository

    def load(
        self,
        experiment_id: str,
        *,
        as_of: datetime,
        instrument_ids: tuple[UUID, ...],
    ):
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        return self._market_contexts.get(
            experiment.dataset_version_id,
            as_of=as_of,
            universe_version_id=experiment.universe_version_id,
            instrument_ids=instrument_ids,
        )
