from __future__ import annotations

from typing import Any

from hope.application.backtests.engine import BacktestResult
from hope.application.backtests.evidence import BACKTEST_EVIDENCE_SCHEMA, project_backtest_result
from hope.application.experiments.config_hash import configuration_hash
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


CERTIFIED_BACKTEST_EVIDENCE_SCHEMA = "hope.certified-backtest-result.v1"
CERTIFIED_BACKTEST_EVIDENCE_FIELDS = (
    "schema",
    "execution_provenance",
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


def _canonical_text(value: str, error: str) -> str:
    if not isinstance(value, str):
        raise TypeError(error)
    if not value.strip() or value != value.strip():
        raise ValueError(error)
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
        "configuration_hash": _canonical_text(
            execution_plan.configuration_hash,
            "CERTIFIED_BACKTEST_EVIDENCE_CONFIGURATION_HASH_NOT_CANONICAL",
        ),
    }


def project_certified_backtest_result(
    result: BacktestResult,
    execution_plan: CertifiedExecutionPlan,
) -> dict[str, Any]:
    """Bind deterministic backtest evidence to its resolved immutable execution provenance."""
    if not isinstance(result, BacktestResult):
        raise TypeError("CERTIFIED_BACKTEST_EVIDENCE_REQUIRES_BACKTEST_RESULT")

    projected = {
        "schema": CERTIFIED_BACKTEST_EVIDENCE_SCHEMA,
        "execution_provenance": _execution_provenance(execution_plan),
        "backtest": project_backtest_result(result),
    }
    if tuple(projected) != CERTIFIED_BACKTEST_EVIDENCE_FIELDS:
        raise RuntimeError("CERTIFIED_BACKTEST_EVIDENCE_V1_FIELD_CONTRACT_BROKEN")
    return projected


def is_certified_backtest_evidence(value: object) -> bool:
    return isinstance(value, dict) and value.get("schema") == CERTIFIED_BACKTEST_EVIDENCE_SCHEMA


def verify_certified_backtest_evidence(
    value: object,
    execution_plan: CertifiedExecutionPlan,
) -> dict[str, Any]:
    """Verify stored certified evidence against the currently resolved execution plan."""
    if not is_certified_backtest_evidence(value):
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_SCHEMA_MISMATCH")
    assert isinstance(value, dict)

    provenance = value.get("execution_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_PROVENANCE_REQUIRED")
    if set(provenance) != set(CERTIFIED_BACKTEST_PROVENANCE_FIELDS):
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_PROVENANCE_FIELDS_MISMATCH")
    if provenance != _execution_provenance(execution_plan):
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_EXECUTION_PROVENANCE_MISMATCH")

    backtest = value.get("backtest")
    if not isinstance(backtest, dict) or backtest.get("schema") != BACKTEST_EVIDENCE_SCHEMA:
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_BACKTEST_SCHEMA_MISMATCH")
    return value


def is_legacy_uncertified_backtest_evidence(value: object) -> bool:
    """Identify durable v1 backtest evidence that predates certified provenance binding."""
    return isinstance(value, dict) and value.get("schema") == BACKTEST_EVIDENCE_SCHEMA
