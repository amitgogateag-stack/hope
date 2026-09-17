from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from hope.application.backtests.engine import BacktestResult
from hope.application.backtests.evidence import BACKTEST_EVIDENCE_SCHEMA, project_backtest_result
from hope.application.experiments.config_hash import configuration_hash
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


CERTIFIED_BACKTEST_EVIDENCE_SCHEMA = "hope.certified-backtest-result.v2"
LEGACY_CERTIFIED_BACKTEST_EVIDENCE_SCHEMA = "hope.certified-backtest-result.v1"
CERTIFIED_BACKTEST_EVIDENCE_FIELDS = (
    "schema",
    "execution_provenance",
    "research_provenance",
    "backtest",
)
CERTIFIED_BACKTEST_PROVENANCE_FIELDS = (
    "experiment_id",
    "strategy_version_id",
    "strategy_id",
    "strategy_version",
    "code_commit",
    "configuration_hash",
)
CERTIFIED_RESEARCH_PROVENANCE_FIELDS = (
    "experiment_id",
    "strategy_version_id",
    "dataset_version_id",
    "universe_version_id",
    "universe_membership_hash",
    "configuration_hash",
    "environment",
    "as_of",
    "run_fingerprint",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_text(value: str, error: str) -> str:
    if not isinstance(value, str):
        raise TypeError(error)
    if not value.strip() or value != value.strip():
        raise ValueError(error)
    return value


def _canonical_uuid(value: object, error: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(error) from exc
    canonical = str(parsed)
    if str(value) != canonical:
        raise ValueError(error)
    return canonical


def _canonical_sha256(value: object, error: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(error)
    return value


def _canonical_as_of(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("CERTIFIED_BACKTEST_EVIDENCE_AS_OF_NOT_CANONICAL")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_AS_OF_NOT_CANONICAL") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.isoformat() != value:
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_AS_OF_NOT_CANONICAL")
    return value


def _execution_provenance(execution_plan: CertifiedExecutionPlan) -> dict[str, str]:
    if not isinstance(execution_plan, CertifiedExecutionPlan):
        raise TypeError("CERTIFIED_BACKTEST_EVIDENCE_REQUIRES_EXECUTION_PLAN")
    if configuration_hash(execution_plan.configuration) != execution_plan.configuration_hash:
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_CONFIGURATION_HASH_MISMATCH")
    return {
        "experiment_id": _canonical_text(
            execution_plan.experiment_id,
            "CERTIFIED_BACKTEST_EVIDENCE_EXPERIMENT_ID_NOT_CANONICAL",
        ),
        "strategy_version_id": str(execution_plan.strategy_version_id),
        "strategy_id": str(execution_plan.strategy_id),
        "strategy_version": _canonical_text(
            execution_plan.strategy_version,
            "CERTIFIED_BACKTEST_EVIDENCE_STRATEGY_VERSION_NOT_CANONICAL",
        ),
        "code_commit": _canonical_text(
            execution_plan.code_commit,
            "CERTIFIED_BACKTEST_EVIDENCE_CODE_COMMIT_NOT_CANONICAL",
        ),
        "configuration_hash": _canonical_sha256(
            execution_plan.configuration_hash,
            "CERTIFIED_BACKTEST_EVIDENCE_CONFIGURATION_HASH_NOT_CANONICAL",
        ),
    }


def _research_provenance(value: Mapping[str, object]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("CERTIFIED_BACKTEST_EVIDENCE_REQUIRES_RESEARCH_PROVENANCE")
    if set(value) != set(CERTIFIED_RESEARCH_PROVENANCE_FIELDS):
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_RESEARCH_PROVENANCE_FIELDS_MISMATCH")
    return {
        "experiment_id": _canonical_text(
            value["experiment_id"],
            "CERTIFIED_BACKTEST_EVIDENCE_EXPERIMENT_ID_NOT_CANONICAL",
        ),
        "strategy_version_id": _canonical_uuid(
            value["strategy_version_id"],
            "CERTIFIED_BACKTEST_EVIDENCE_STRATEGY_VERSION_ID_NOT_CANONICAL",
        ),
        "dataset_version_id": _canonical_uuid(
            value["dataset_version_id"],
            "CERTIFIED_BACKTEST_EVIDENCE_DATASET_VERSION_ID_NOT_CANONICAL",
        ),
        "universe_version_id": _canonical_uuid(
            value["universe_version_id"],
            "CERTIFIED_BACKTEST_EVIDENCE_UNIVERSE_VERSION_ID_NOT_CANONICAL",
        ),
        "universe_membership_hash": _canonical_sha256(
            value["universe_membership_hash"],
            "CERTIFIED_BACKTEST_EVIDENCE_UNIVERSE_MEMBERSHIP_HASH_NOT_CANONICAL",
        ),
        "configuration_hash": _canonical_sha256(
            value["configuration_hash"],
            "CERTIFIED_BACKTEST_EVIDENCE_CONFIGURATION_HASH_NOT_CANONICAL",
        ),
        "environment": _canonical_text(
            value["environment"],
            "CERTIFIED_BACKTEST_EVIDENCE_ENVIRONMENT_NOT_CANONICAL",
        ),
        "as_of": _canonical_as_of(value["as_of"]),
        "run_fingerprint": _canonical_sha256(
            value["run_fingerprint"],
            "CERTIFIED_BACKTEST_EVIDENCE_RUN_FINGERPRINT_NOT_CANONICAL",
        ),
    }


def _verify_cross_provenance(
    execution_provenance: Mapping[str, str],
    research_provenance: Mapping[str, str],
) -> None:
    for field in ("experiment_id", "strategy_version_id", "configuration_hash"):
        if execution_provenance[field] != research_provenance[field]:
            raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_CROSS_PROVENANCE_MISMATCH")


def project_certified_backtest_result(
    result: BacktestResult,
    execution_plan: CertifiedExecutionPlan,
    research_provenance: Mapping[str, object],
) -> dict[str, Any]:
    """Bind deterministic backtest evidence to immutable execution and scientific run provenance."""
    if not isinstance(result, BacktestResult):
        raise TypeError("CERTIFIED_BACKTEST_EVIDENCE_REQUIRES_BACKTEST_RESULT")

    execution = _execution_provenance(execution_plan)
    research = _research_provenance(research_provenance)
    _verify_cross_provenance(execution, research)
    projected = {
        "schema": CERTIFIED_BACKTEST_EVIDENCE_SCHEMA,
        "execution_provenance": execution,
        "research_provenance": research,
        "backtest": project_backtest_result(result),
    }
    if tuple(projected) != CERTIFIED_BACKTEST_EVIDENCE_FIELDS:
        raise RuntimeError("CERTIFIED_BACKTEST_EVIDENCE_V2_FIELD_CONTRACT_BROKEN")
    return projected


def is_certified_backtest_evidence(value: object) -> bool:
    return isinstance(value, dict) and value.get("schema") == CERTIFIED_BACKTEST_EVIDENCE_SCHEMA


def is_legacy_certified_backtest_evidence(value: object) -> bool:
    return isinstance(value, dict) and value.get("schema") == LEGACY_CERTIFIED_BACKTEST_EVIDENCE_SCHEMA


def verify_certified_backtest_evidence(
    value: object,
    execution_plan: CertifiedExecutionPlan,
    research_provenance: Mapping[str, object],
) -> dict[str, Any]:
    """Verify stored certified evidence against current execution and scientific run provenance."""
    if not is_certified_backtest_evidence(value):
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_SCHEMA_MISMATCH")
    assert isinstance(value, dict)
    if tuple(value) != CERTIFIED_BACKTEST_EVIDENCE_FIELDS:
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_FIELDS_MISMATCH")

    execution = value.get("execution_provenance")
    if not isinstance(execution, dict):
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_PROVENANCE_REQUIRED")
    if set(execution) != set(CERTIFIED_BACKTEST_PROVENANCE_FIELDS):
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_PROVENANCE_FIELDS_MISMATCH")
    expected_execution = _execution_provenance(execution_plan)
    if execution != expected_execution:
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_EXECUTION_PROVENANCE_MISMATCH")

    research = value.get("research_provenance")
    if not isinstance(research, dict):
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_RESEARCH_PROVENANCE_REQUIRED")
    expected_research = _research_provenance(research_provenance)
    stored_research = _research_provenance(research)
    if stored_research != expected_research:
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_RESEARCH_PROVENANCE_MISMATCH")
    _verify_cross_provenance(expected_execution, stored_research)

    backtest = value.get("backtest")
    if not isinstance(backtest, dict) or backtest.get("schema") != BACKTEST_EVIDENCE_SCHEMA:
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_BACKTEST_SCHEMA_MISMATCH")
    return value


def is_legacy_uncertified_backtest_evidence(value: object) -> bool:
    """Identify durable bare backtest evidence that predates certified provenance binding."""
    return isinstance(value, dict) and value.get("schema") == BACKTEST_EVIDENCE_SCHEMA
