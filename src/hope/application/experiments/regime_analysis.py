from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS


REGIME_ANALYSIS_EVIDENCE_SCHEMA = "hope.regime-analysis-evidence.v1"


class RegimeAnalysisStageEvaluator:
    """Compare predeclared regime metrics without deriving a verdict."""

    stage = "regime_analysis"

    def evaluate(
        self,
        *,
        stage_protocol: Mapping[str, Any],
        control_evidence: Any,
        variant_evidence: Any,
    ) -> dict[str, Any]:
        regime_ids = stage_protocol.get("regime_ids")
        if not isinstance(regime_ids, list) or not regime_ids:
            raise ValueError("REGIME_ANALYSIS_REGIMES_PREDECLARATION_REQUIRED")
        if any(not isinstance(regime_id, str) or not regime_id.strip() for regime_id in regime_ids):
            raise ValueError("REGIME_ANALYSIS_REGIME_ID_INVALID")
        if len(set(regime_ids)) != len(regime_ids):
            raise ValueError("REGIME_ANALYSIS_REGIME_ID_DUPLICATE")

        definitions = stage_protocol.get("regime_definitions")
        if not isinstance(definitions, dict) or set(definitions) != set(regime_ids):
            raise ValueError("REGIME_ANALYSIS_DEFINITIONS_PREDECLARATION_REQUIRED")
        for regime_id in regime_ids:
            definition = definitions.get(regime_id)
            if not isinstance(definition, dict) or not definition:
                raise ValueError(f"REGIME_ANALYSIS_DEFINITION_INVALID:{regime_id}")

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("REGIME_ANALYSIS_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("REGIME_ANALYSIS_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("REGIME_ANALYSIS_METRIC_DUPLICATE")

        control = self._artifact(control_evidence)
        variant = self._artifact(variant_evidence)
        control_regimes = self._regimes_by_id(control)
        variant_regimes = self._regimes_by_id(variant)

        if list(control_regimes) != regime_ids:
            raise ValueError("REGIME_ANALYSIS_CONTROL_REGIMES_MISMATCH")
        if list(variant_regimes) != regime_ids:
            raise ValueError("REGIME_ANALYSIS_VARIANT_REGIMES_MISMATCH")

        comparisons: dict[str, Any] = {}
        for regime_id in regime_ids:
            control_item = control_regimes[regime_id]
            variant_item = variant_regimes[regime_id]
            expected = definitions[regime_id]
            if (
                control_item["regime_definition"] != expected
                or variant_item["regime_definition"] != expected
            ):
                raise ValueError(
                    f"REGIME_ANALYSIS_PREDECLARED_DEFINITION_MISMATCH:{regime_id}"
                )
            control_metrics = control_item["metrics"]
            variant_metrics = variant_item["metrics"]
            compared: dict[str, dict[str, str]] = {}
            for metric in metrics:
                control_value = self._decimal(control_metrics.get(metric), metric)
                variant_value = self._decimal(variant_metrics.get(metric), metric)
                compared[metric] = {
                    "control": self._canonical_decimal(control_value),
                    "variant": self._canonical_decimal(variant_value),
                    "delta": self._canonical_decimal(variant_value - control_value),
                }
            comparisons[regime_id] = {
                "regime_definition": expected,
                "metrics": compared,
            }

        return {
            "schema": "hope.regime-analysis-evaluation.v1",
            "regimes": comparisons,
        }

    @staticmethod
    def _artifact(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("schema") != REGIME_ANALYSIS_EVIDENCE_SCHEMA:
            raise ValueError("REGIME_ANALYSIS_EVIDENCE_REQUIRED")
        regimes = value.get("regimes")
        fingerprint = value.get("result_fingerprint")
        if not isinstance(regimes, list) or not regimes or not isinstance(fingerprint, str):
            raise ValueError("REGIME_ANALYSIS_EVIDENCE_REQUIRED")
        if configuration_hash(regimes) != fingerprint:
            raise ValueError("REGIME_ANALYSIS_EVIDENCE_FINGERPRINT_MISMATCH")
        return value

    @staticmethod
    def _regimes_by_id(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for regime in value["regimes"]:
            if not isinstance(regime, dict):
                raise ValueError("REGIME_ANALYSIS_REGIME_INVALID")
            regime_id = regime.get("regime_id")
            definition = regime.get("regime_definition")
            metrics = regime.get("metrics")
            if not isinstance(regime_id, str) or not regime_id.strip() or not isinstance(metrics, dict):
                raise ValueError("REGIME_ANALYSIS_REGIME_INVALID")
            if regime_id in result:
                raise ValueError("REGIME_ANALYSIS_REGIME_ID_DUPLICATE")
            result[regime_id] = regime
        return result

    @staticmethod
    def _decimal(value: Any, metric: str) -> Decimal:
        if not isinstance(value, str):
            raise ValueError(f"REGIME_ANALYSIS_METRIC_VALUE_INVALID:{metric}")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"REGIME_ANALYSIS_METRIC_VALUE_INVALID:{metric}") from exc
        if not parsed.is_finite():
            raise ValueError(f"REGIME_ANALYSIS_METRIC_VALUE_INVALID:{metric}")
        return parsed

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
