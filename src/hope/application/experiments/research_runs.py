from __future__ import annotations

import hashlib
import json
from datetime import datetime

from hope.application.backtests.certified_evidence import (
    is_certified_backtest_evidence,
    is_legacy_certified_backtest_evidence,
    is_legacy_run_certified_backtest_evidence,
    is_legacy_uncertified_backtest_evidence,
    project_certified_backtest_result,
    verify_certified_backtest_evidence,
)
from hope.application.backtests.engine import BacktestResult
from hope.application.backtests.evidence import project_backtest_result
from hope.application.experiments.execution import CertifiedResearchExecutor, CertifiedResearchInputs
from hope.application.universe.snapshot import UniverseSnapshot
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan
from hope.infrastructure.repositories.experiments import ExperimentRecord


def _canonical_json(value) -> tuple[object, str]:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return json.loads(encoded), encoded


def research_run_provenance(
    experiment: ExperimentRecord,
    *,
    as_of: datetime,
    universe_snapshot: UniverseSnapshot,
    market_data_manifest_hash: str,
) -> dict[str, str]:
    """Return the canonical scientific identity embedded into certified result evidence."""
    _require_active_experiment(experiment)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("RESEARCH_RUN_AS_OF_MUST_BE_TIMEZONE_AWARE")
    if not isinstance(universe_snapshot, UniverseSnapshot):
        raise TypeError("RESEARCH_RUN_REQUIRES_UNIVERSE_SNAPSHOT")
    if universe_snapshot.universe_version_id != experiment.universe_version_id:
        raise ValueError("RESEARCH_RUN_UNIVERSE_VERSION_MISMATCH")
    if universe_snapshot.version.pit_certified is not True:
        raise ValueError("RESEARCH_RUN_REQUIRES_PIT_CERTIFIED_UNIVERSE")
    if (
        not isinstance(market_data_manifest_hash, str)
        or len(market_data_manifest_hash) != 64
        or any(character not in "0123456789abcdef" for character in market_data_manifest_hash)
    ):
        raise ValueError("RESEARCH_RUN_REQUIRES_CANONICAL_MARKET_DATA_MANIFEST_HASH")

    identity = {
        "experiment_id": experiment.experiment_id,
        "strategy_version_id": str(experiment.strategy_version_id),
        "dataset_version_id": str(experiment.dataset_version_id),
        "universe_version_id": str(experiment.universe_version_id),
        "universe_membership_hash": universe_snapshot.membership_hash,
        "market_data_manifest_hash": market_data_manifest_hash,
        "configuration_hash": experiment.configuration_hash,
        "environment": experiment.environment,
        "as_of": as_of.isoformat(),
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return {
        **identity,
        "run_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def research_result_fingerprint(
    result,
    *,
    execution_plan: CertifiedExecutionPlan | None = None,
    research_provenance: dict[str, str] | None = None,
) -> tuple[object, str]:
    """Return canonical JSON evidence and its deterministic SHA256 identity.

    Certified backtests bind the stored evidence to both resolved immutable
    execution provenance and exact scientific run provenance. Generic research
    handlers retain their existing canonical result contract.
    """
    if isinstance(result, BacktestResult):
        if execution_plan is None:
            if research_provenance is not None:
                raise ValueError("RESEARCH_RUN_CERTIFIED_BACKTEST_EXECUTION_PLAN_REQUIRED")
            result = project_backtest_result(result)
        else:
            if research_provenance is None:
                raise ValueError("RESEARCH_RUN_CERTIFIED_BACKTEST_RUN_PROVENANCE_REQUIRED")
            result = project_certified_backtest_result(
                result,
                execution_plan,
                research_provenance,
            )
    canonical, encoded = _canonical_json(result)
    return canonical, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_research_result_evidence(
    canonical_result,
    result_fingerprint: str,
    *,
    reject_legacy_backtest: bool = False,
    execution_plan: CertifiedExecutionPlan | None = None,
    research_provenance: dict[str, str] | None = None,
) -> object:
    """Independently reconstruct and verify immutable stored result evidence."""
    if reject_legacy_backtest and is_legacy_uncertified_backtest_evidence(canonical_result):
        raise ValueError("RESEARCH_RUN_CERTIFIED_BACKTEST_PROVENANCE_MISSING")
    if reject_legacy_backtest and is_legacy_run_certified_backtest_evidence(canonical_result):
        raise ValueError("RESEARCH_RUN_CERTIFIED_BACKTEST_MANIFEST_PROVENANCE_MISSING")
    if reject_legacy_backtest and is_legacy_certified_backtest_evidence(canonical_result):
        raise ValueError("RESEARCH_RUN_CERTIFIED_BACKTEST_RUN_PROVENANCE_MISSING")
    canonical, reconstructed = research_result_fingerprint(canonical_result)
    if reconstructed != result_fingerprint:
        raise ValueError("RESEARCH_RUN_EVIDENCE_FINGERPRINT_MISMATCH")
    if is_certified_backtest_evidence(canonical):
        if execution_plan is None:
            raise ValueError("RESEARCH_RUN_CERTIFIED_BACKTEST_EXECUTION_PLAN_REQUIRED")
        if research_provenance is None:
            raise ValueError("RESEARCH_RUN_CERTIFIED_BACKTEST_RUN_PROVENANCE_REQUIRED")
        verify_certified_backtest_evidence(
            canonical,
            execution_plan,
            research_provenance,
        )
    return canonical


def _require_active_experiment(experiment: ExperimentRecord) -> ExperimentRecord:
    if experiment.invalidated_at is not None:
        raise ValueError("RESEARCH_EXPERIMENT_INVALIDATED")
    return experiment


def research_run_fingerprint(
    experiment: ExperimentRecord,
    *,
    as_of: datetime,
    universe_snapshot: UniverseSnapshot,
    market_data_manifest_hash: str,
) -> str:
    """Bind run identity to immutable experiment, frozen universe and sealed market data."""
    return research_run_provenance(
        experiment,
        as_of=as_of,
        universe_snapshot=universe_snapshot,
        market_data_manifest_hash=market_data_manifest_hash,
    )["run_fingerprint"]


class CertifiedResearchInputLoader:
    """Resolve authoritative universe and PIT market evidence only from experiment provenance."""

    def __init__(self, market_context_repository, universe_snapshot_repository) -> None:
        self._market_contexts = market_context_repository
        self._universes = universe_snapshot_repository

    def universe(self, experiment: ExperimentRecord) -> UniverseSnapshot:
        _require_active_experiment(experiment)
        snapshot = self._universes.get(experiment.universe_version_id)
        if snapshot is None:
            raise ValueError("RESEARCH_RUN_UNIVERSE_SNAPSHOT_MISSING")
        if snapshot.universe_version_id != experiment.universe_version_id:
            raise ValueError("RESEARCH_RUN_UNIVERSE_VERSION_MISMATCH")
        if snapshot.version.pit_certified is not True:
            raise ValueError("RESEARCH_RUN_REQUIRES_PIT_CERTIFIED_UNIVERSE")
        return snapshot

    def market_data_manifest_hash(self, experiment: ExperimentRecord) -> str:
        _require_active_experiment(experiment)
        resolver = getattr(self._market_contexts, "manifest_hash", None)
        if not callable(resolver):
            raise TypeError("RESEARCH_RUN_REQUIRES_MARKET_DATA_MANIFEST_IDENTITY_RESOLVER")
        value = resolver(
            experiment.dataset_version_id,
            universe_version_id=experiment.universe_version_id,
        )
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("RESEARCH_RUN_REQUIRES_CANONICAL_MARKET_DATA_MANIFEST_HASH")
        return value

    def load(
        self,
        experiment: ExperimentRecord,
        *,
        as_of: datetime,
        universe_snapshot: UniverseSnapshot,
    ) -> CertifiedResearchInputs:
        _require_active_experiment(experiment)
        if universe_snapshot.universe_version_id != experiment.universe_version_id:
            raise ValueError("RESEARCH_RUN_UNIVERSE_VERSION_MISMATCH")
        if universe_snapshot.version.pit_certified is not True:
            raise ValueError("RESEARCH_RUN_REQUIRES_PIT_CERTIFIED_UNIVERSE")
        instrument_ids = universe_snapshot.active_instrument_ids(as_of)
        context = self._market_contexts.get(
            experiment.dataset_version_id,
            as_of=as_of,
            universe_version_id=experiment.universe_version_id,
            instrument_ids=instrument_ids,
        )
        if context.as_of != as_of:
            raise ValueError("RESEARCH_RUN_MARKET_CONTEXT_AS_OF_MISMATCH")
        return CertifiedResearchInputs(
            market_context=context,
            universe_snapshot=universe_snapshot,
        )


def _require_certified_executor(executor: CertifiedResearchExecutor) -> CertifiedResearchExecutor:
    if not isinstance(executor, CertifiedResearchExecutor):
        raise TypeError("RESEARCH_RUN_REQUIRES_CERTIFIED_EXECUTOR")
    return executor


class CertifiedResearchRunOrchestrator:
    """Execute only immutable certified universe/PIT inputs with verified execution provenance."""

    def __init__(
        self,
        experiment_repository,
        run_repository,
        market_context_repository,
        universe_snapshot_repository,
        execution_plan_resolver,
        certified_executor: CertifiedResearchExecutor,
        evidence_repository=None,
    ) -> None:
        self._experiments = experiment_repository
        self._runs = run_repository
        self._inputs = CertifiedResearchInputLoader(
            market_context_repository,
            universe_snapshot_repository,
        )
        self._execution_plans = execution_plan_resolver
        self._executor = _require_certified_executor(certified_executor)
        self._evidence = evidence_repository

    def execute(
        self,
        experiment_id: str,
        *,
        as_of: datetime,
    ):
        from hope.infrastructure.repositories.research_run_evidence import ResearchRunEvidenceRecord
        from hope.infrastructure.repositories.research_runs import ResearchRunRecord

        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        _require_active_experiment(experiment)

        execution_plan = self._execution_plans.resolve(experiment)
        self._executor.validate(execution_plan)
        universe_snapshot = self._inputs.universe(experiment)
        market_data_manifest_hash = self._inputs.market_data_manifest_hash(experiment)
        provenance = research_run_provenance(
            experiment,
            as_of=as_of,
            universe_snapshot=universe_snapshot,
            market_data_manifest_hash=market_data_manifest_hash,
        )
        fingerprint = provenance["run_fingerprint"]
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
                    reject_legacy_backtest=True,
                    execution_plan=execution_plan,
                    research_provenance=provenance,
                )
                return run, verified

        inputs = self._inputs.load(
            experiment,
            as_of=as_of,
            universe_snapshot=universe_snapshot,
        )
        result = self._executor.execute(execution_plan, inputs)
        if self._evidence is None:
            return run, result

        canonical_result, result_fingerprint = research_result_fingerprint(
            result,
            execution_plan=execution_plan,
            research_provenance=provenance,
        )
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
            reject_legacy_backtest=True,
            execution_plan=execution_plan,
            research_provenance=provenance,
        )
        if persisted.result_fingerprint != result_fingerprint or verified != canonical_result:
            raise ValueError("RESEARCH_RUN_EVIDENCE_PERSISTED_RESULT_MISMATCH")
        return run, verified


class CertifiedResearchReproducibilityVerifier:
    """Re-execute frozen universe/PIT inputs and compare them with durable evidence."""

    def __init__(
        self,
        experiment_repository,
        run_repository,
        market_context_repository,
        universe_snapshot_repository,
        execution_plan_resolver,
        certified_executor: CertifiedResearchExecutor,
        evidence_repository,
    ) -> None:
        self._experiments = experiment_repository
        self._runs = run_repository
        self._inputs = CertifiedResearchInputLoader(
            market_context_repository,
            universe_snapshot_repository,
        )
        self._execution_plans = execution_plan_resolver
        self._executor = _require_certified_executor(certified_executor)
        self._evidence = evidence_repository

    def verify(
        self,
        experiment_id: str,
        *,
        as_of: datetime,
    ) -> object:
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        _require_active_experiment(experiment)

        execution_plan = self._execution_plans.resolve(experiment)
        self._executor.validate(execution_plan)
        universe_snapshot = self._inputs.universe(experiment)
        market_data_manifest_hash = self._inputs.market_data_manifest_hash(experiment)
        provenance = research_run_provenance(
            experiment,
            as_of=as_of,
            universe_snapshot=universe_snapshot,
            market_data_manifest_hash=market_data_manifest_hash,
        )
        fingerprint = provenance["run_fingerprint"]
        run_id = self._runs.deterministic_id(experiment_id, fingerprint)
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError("RESEARCH_REPRODUCIBILITY_RUN_MISSING")
        if run.experiment_id != experiment_id or run.run_fingerprint != fingerprint or run.as_of != as_of:
            raise ValueError("RESEARCH_REPRODUCIBILITY_RUN_IDENTITY_MISMATCH")

        evidence = self._evidence.get(run_id)
        if evidence is None:
            raise ValueError("RESEARCH_REPRODUCIBILITY_EVIDENCE_MISSING")
        stored = verify_research_result_evidence(
            evidence.canonical_result,
            evidence.result_fingerprint,
            reject_legacy_backtest=True,
            execution_plan=execution_plan,
            research_provenance=provenance,
        )

        inputs = self._inputs.load(
            experiment,
            as_of=as_of,
            universe_snapshot=universe_snapshot,
        )
        reproduced_result = self._executor.execute(execution_plan, inputs)
        reproduced, reproduced_fingerprint = research_result_fingerprint(
            reproduced_result,
            execution_plan=execution_plan,
            research_provenance=provenance,
        )
        if reproduced_fingerprint != evidence.result_fingerprint or reproduced != stored:
            raise ValueError("RESEARCH_REPRODUCIBILITY_MISMATCH")
        return reproduced
