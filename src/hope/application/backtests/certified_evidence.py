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


def _canonical_text(value: str, error: str) -> str:
    if not isinstance(value, str):
        raise TypeError(error)
    if not value.strip() or value != value.strip():
        raise ValueError(error)
    return value


def project_certified_backtest_result(
    result: BacktestResult,
    execution_plan: CertifiedExecutionPlan,
) -> dict[str, Any]:
    """Bind deterministic backtest evidence to its resolved immutable execution provenance."""
    if not isinstance(result, BacktestResult):
        raise TypeError("CERTIFIED_BACKTEST_EVIDENCE_REQUIRES_BACKTEST_RESULT")
    if not isinstance(execution_plan, CertifiedExecutionPlan):
        raise TypeError("CERTIFIED_BACKTEST_EVIDENCE_REQUIRES_EXECUTION_PLAN")
    if configuration_hash(execution_plan.configuration) != execution_plan.configuration_hash:
        raise ValueError("CERTIFIED_BACKTEST_EVIDENCE_CONFIGURATION_HASH_MISMATCH")

    provenance = {
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
    projected = {
        "schema": CERTIFIED_BACKTEST_EVIDENCE_SCHEMA,
        "execution_provenance": provenance,
        "backtest": project_backtest_result(result),
    }
    if tuple(projected) != CERTIFIED_BACKTEST_EVIDENCE_FIELDS:
        raise RuntimeError("CERTIFIED_BACKTEST_EVIDENCE_V1_FIELD_CONTRACT_BROKEN")
    return projected


def is_legacy_uncertified_backtest_evidence(value: object) -> bool:
    """Identify durable v1 backtest evidence that predates certified provenance binding."""
    return isinstance(value, dict) and value.get("schema") == BACKTEST_EVIDENCE_SCHEMA
