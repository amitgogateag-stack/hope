from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from hope.application.backtests.engine import BacktestResult
from hope.application.backtests.evidence import project_backtest_result
from hope.application.experiments.execution import CertifiedResearchExecutor
from hope.infrastructure.repositories.experiments import ExperimentRecord


def _canonical_json(value) -> tuple[object, str]:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return json.loads(encoded), encoded


def research_result_fingerprint(result) -> tuple[object, str]:
    """Return canonical JSON evidence and its deterministic SHA256 identity."""
    if isinstance(result, BacktestResult):
        result = project_backtest_result(result)
    canonical, encoded = _canonical_json(result)
    return canonical, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_research_result_evidence(canonical_result, result_fingerprint: str) -> object:
    """Independently reconstruct and verify immutable stored result evidence."""
    canonical, reconstructed = research_result_fingerprint(canonical_result)
    if reconstructed != result_fingerprint:
        raise ValueError("RESEARCH_RUN_EVIDENCE_FINGERPRINT_MISMATCH")
    return canonical


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


def _require_certified_executor(executor: CertifiedResearchExecutor) -> CertifiedResearchExecutor:
    if not isinstance(executor, CertifiedResearchExecutor):
        raise TypeError("RESEARCH_RUN_REQUIRES_CERTIFIED_EXECUTOR")
    return executor


class CertifiedResearchRunOrchestrator:
    """Execute only certified PIT context with verified immutable execution provenance."""

    def __init__(
        self,
        experiment_repository,
        run_repository,
        market_context_repository,
        execution_plan_resolver,
        certified_executor: CertifiedResearchExecutor,
        evidence_repository=None,
    ) -> None:
        self._experiments = experiment_repository
        self._runs = run_repository
        self._contexts = CertifiedResearchContextLoader(experiment_repository, market_context_repository)
        self._execution_plans = execution_plan_resolver
        self._executor = _require_certified_executor(certified_executor)
        self._evidence = evidence_repository

    def execute(
        self,
        experiment_id: str,
        *,
        as_of: datetime,
        instrument_ids: tuple[UUID, ...],
    ):
        from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord
        from hope.infrastructure.repositories.research_runs import ResearchRunRecord

        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise KeyError(f"unknown experiment: {experiment_id}")

        execution_plan = self._execution_plans.resolve(experiment)

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
                verified = verify_research_result_evidence(
                    existing.canonical_result,
                    existing.result_fingerprint,
                )
                return run, verified

        context = self._contexts.load(experiment_id, as_of=as_of, instrument_ids=instrument_ids)
        result = self._executor.execute(execution_plan, context)
        if self._evidence is None:
            return run, result

        canonical_result, result_fingerprint = research_result_fingerprint(result)
        evidence = ResearchRunEvidenceRecord(
            research_run_id=run_id,
            result_fingerprint=result_fingerprint,
            canonical_result=canonical_result,
        )
        self._evidence.persist(evidence)
        persisted = self._evidence.get(run_id)
        if persisted is None:
            raise RuntimeError("RESEARCH_RUN_EVIDENCE_PERSIST_LOST")
        verified = verify_research_result_evidence(
            persisted.canonical_result,
            persisted.result_fingerprint,
        )
        if persisted.result_fingerprint != result_fingerprint or verified != canonical_result:
            raise ValueError("RESEARCH_RUN_EVIDENCE_PERSISTED_RESULT_MISMATCH")
        return run, verified


class CertifiedResearchReproducibilityVerifier:
    """Re-execute certified immutable inputs and compare them with durable evidence."""

    def __init__(
        self,
        experiment_repository,
        run_repository,
        market_context_repository,
        execution_plan_resolver,
        certified_executor: CertifiedResearchExecutor,
        evidence_repository,
    ) -> None:
        self._experiments = experiment_repository
        self._runs = run_repository
        self._contexts = CertifiedResearchContextLoader(experiment_repository, market_context_repository)
        self._execution_plans = execution_plan_resolver
        self._executor = _require_certified_executor(certified_executor)
        self._evidence = evidence_repository

    def verify(
        self,
        experiment_id: str,
        *,
        as_of: datetime,
        instrument_ids: tuple[UUID, ...],
    ) -> object:
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise KeyError(f"unknown experiment: {experiment_id}")

        execution_plan = self._execution_plans.resolve(experiment)
        fingerprint = research_run_fingerprint(experiment, as_of=as_of, instrument_ids=instrument_ids)
        run_id = self._runs.deterministic_id(experiment_id, fingerprint)
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError("RESEARCH_REPRODUCIBILITY_RUN_MISSING")
        if run.experiment_id != experiment_id or run.run_fingerprint != fingerprint or run.as_of != as_of:
            raise ValueError("RESEARCH_REPRODUCIBILITY_RUN_IDENTITY_MISMATCH")

        evidence = self._evidence.get(run_id)
        if evidence is None:
            raise ValueError("RESEARCH_REPRODUCIBILITY_EVIDENCE_MISSING")
        stored = verify_research_result_evidence(evidence.canonical_result, evidence.result_fingerprint)

        context = self._contexts.load(experiment_id, as_of=as_of, instrument_ids=instrument_ids)
        reproduced_result = self._executor.execute(execution_plan, context)
        reproduced, reproduced_fingerprint = research_result_fingerprint(reproduced_result)
        if reproduced_fingerprint != evidence.result_fingerprint or reproduced != stored:
            raise ValueError("RESEARCH_REPRODUCIBILITY_MISMATCH")
        return reproduced
