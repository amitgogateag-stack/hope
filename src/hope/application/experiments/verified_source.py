from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from hope.application.experiments.research_runs import verify_research_result_evidence


_RESEARCH_IDENTITY_FIELDS = (
    "experiment_id",
    "strategy_version_id",
    "dataset_version_id",
    "universe_version_id",
    "universe_membership_hash",
    "market_data_manifest_hash",
    "configuration_hash",
    "environment",
    "as_of",
)


class VerifiedCertifiedResearchSourceResolver:
    """Resolve one durable research run into independently verified certified evidence."""

    def __init__(
        self,
        *,
        experiment_repository,
        run_repository,
        evidence_repository,
        execution_plan_resolver,
        universe_snapshot_repository,
        market_context_repository,
    ) -> None:
        self._experiments = experiment_repository
        self._runs = run_repository
        self._evidence = evidence_repository
        self._execution_plans = execution_plan_resolver
        self._universes = universe_snapshot_repository
        self._market_contexts = market_context_repository

    def resolve(self, source_research_run_id: UUID) -> dict[str, Any]:
        if not isinstance(source_research_run_id, UUID):
            raise TypeError("VERIFIED_RESEARCH_SOURCE_RUN_ID_REQUIRED")

        run = self._runs.get(source_research_run_id)
        if run is None:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_RUN_MISSING")
        if run.research_run_id != source_research_run_id:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_RUN_IDENTITY_MISMATCH")

        experiment = self._experiments.get(run.experiment_id)
        if experiment is None:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_EXPERIMENT_MISSING")
        if experiment.experiment_id != run.experiment_id:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_EXPERIMENT_IDENTITY_MISMATCH")
        if experiment.invalidated_at is not None:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_EXPERIMENT_INVALIDATED")

        evidence = self._evidence.get(source_research_run_id)
        if evidence is None:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_EVIDENCE_MISSING")
        if evidence.research_run_id != source_research_run_id:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_EVIDENCE_IDENTITY_MISMATCH")

        canonical = evidence.canonical_result
        if not isinstance(canonical, dict):
            raise ValueError("VERIFIED_RESEARCH_SOURCE_CERTIFIED_EVIDENCE_REQUIRED")
        research = canonical.get("research_provenance")
        if not isinstance(research, dict):
            raise ValueError("VERIFIED_RESEARCH_SOURCE_RESEARCH_PROVENANCE_REQUIRED")

        expected_fields = set(_RESEARCH_IDENTITY_FIELDS) | {"run_fingerprint"}
        if set(research) != expected_fields:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_RESEARCH_PROVENANCE_FIELDS_MISMATCH")

        universe_snapshot = self._universes.get(experiment.universe_version_id)
        if universe_snapshot is None:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_UNIVERSE_SNAPSHOT_MISSING")
        if universe_snapshot.universe_version_id != experiment.universe_version_id:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_UNIVERSE_SNAPSHOT_IDENTITY_MISMATCH")
        if universe_snapshot.version.pit_certified is not True:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_UNIVERSE_NOT_PIT_CERTIFIED")

        authoritative_membership_hash = universe_snapshot.membership_hash
        if research.get("universe_membership_hash") != authoritative_membership_hash:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_UNIVERSE_MEMBERSHIP_HASH_MISMATCH")

        authoritative_manifest_hash = self._market_contexts.manifest_hash(
            experiment.dataset_version_id,
            universe_version_id=experiment.universe_version_id,
        )
        if research.get("market_data_manifest_hash") != authoritative_manifest_hash:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_MARKET_DATA_MANIFEST_HASH_MISMATCH")

        expected_identity = {
            "experiment_id": experiment.experiment_id,
            "strategy_version_id": str(experiment.strategy_version_id),
            "dataset_version_id": str(experiment.dataset_version_id),
            "universe_version_id": str(experiment.universe_version_id),
            "universe_membership_hash": authoritative_membership_hash,
            "market_data_manifest_hash": authoritative_manifest_hash,
            "configuration_hash": experiment.configuration_hash,
            "environment": experiment.environment,
            "as_of": run.as_of.isoformat(),
        }
        observed_identity = {field: research.get(field) for field in _RESEARCH_IDENTITY_FIELDS}
        if observed_identity != expected_identity:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_RESEARCH_PROVENANCE_MISMATCH")

        encoded = json.dumps(
            expected_identity,
            sort_keys=True,
            separators=(",", ":"),
        )
        reconstructed_run_fingerprint = hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()
        if (
            research.get("run_fingerprint") != reconstructed_run_fingerprint
            or run.run_fingerprint != reconstructed_run_fingerprint
        ):
            raise ValueError("VERIFIED_RESEARCH_SOURCE_RUN_FINGERPRINT_MISMATCH")

        execution_plan = self._execution_plans.resolve(experiment)
        verified = verify_research_result_evidence(
            canonical,
            evidence.result_fingerprint,
            reject_legacy_backtest=True,
            execution_plan=execution_plan,
            research_provenance=research,
        )
        if not isinstance(verified, dict):
            raise ValueError("VERIFIED_RESEARCH_SOURCE_CERTIFIED_EVIDENCE_REQUIRED")
        return verified
