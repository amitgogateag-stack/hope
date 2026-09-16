from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from hope.infrastructure.repositories.experiments import ExperimentRecord


def _canonical_json(value) -> tuple[object, str]:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return json.loads(encoded), encoded


def research_result_fingerprint(result) -> tuple[object, str]:
    """Return canonical JSON evidence and its deterministic SHA256 identity."""
    canonical, encoded = _canonical_json(result)
    return canonical, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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

    def load(self, experiment_id: str, *, as_of: datetime, instrument_ids: tuple[UUID, ...]):
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        return self._market_contexts.get(
            experiment.dataset_version_id,
            as_of=as_of,
            universe_version_id=experiment.universe_version_id,
            instrument_ids=instrument_ids,
        )


class CertifiedResearchRunOrchestrator:
    """Execute certified PIT evidence and optionally persist immutable canonical output."""

    def __init__(
        self,
        experiment_repository,
        run_repository,
        market_context_repository,
        evidence_repository=None,
    ) -> None:
        self._experiments = experiment_repository
        self._runs = run_repository
        self._contexts = CertifiedResearchContextLoader(experiment_repository, market_context_repository)
        self._evidence = evidence_repository

    def execute(
        self,
        experiment_id: str,
        *,
        as_of: datetime,
        instrument_ids: tuple[UUID, ...],
        execute,
    ):
        from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord
        from hope.infrastructure.repositories.research_runs import ResearchRunRecord

        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        fingerprint = research_run_fingerprint(experiment, as_of=as_of, instrument_ids=instrument_ids)
        run_id = self._runs.deterministic_id(experiment_id, fingerprint)
        run = ResearchRunRecord(
            research_run_id=run_id,
            experiment_id=experiment_id,
            run_fingerprint=fingerprint,
            as_of=as_of,
        )
        self._runs.claim(run)

        if self._evidence is not None:
            existing = self._evidence.get(run_id)
            if existing is not None:
                return run, existing.canonical_result

        context = self._contexts.load(experiment_id, as_of=as_of, instrument_ids=instrument_ids)
        result = execute(context)
        if self._evidence is None:
            return run, result

        canonical_result, result_fingerprint = research_result_fingerprint(result)
        evidence = ResearchRunEvidenceRecord(
            research_run_id=run_id,
            result_fingerprint=result_fingerprint,
            canonical_result=canonical_result,
        )
        self._evidence.persist(evidence)
        return run, canonical_result
