from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS


SLIPPAGE_STRESS_EVIDENCE_SCHEMA = "hope.slippage-stress-evidence.v1"


class SlippageStressStageEvaluator:
    """Compare predeclared slippage scenarios without deriving a verdict."""

    stage = "slippage_stress"

    def evaluate(
        self,
        *,
        stage_protocol: Mapping[str, Any],
        control_evidence: Any,
        variant_evidence: Any,
    ) -> dict[str, Any]:
        scenario_ids = stage_protocol.get("scenario_ids")
        if not isinstance(scenario_ids, list) or not scenario_ids:
            raise ValueError("SLIPPAGE_STRESS_SCENARIOS_PREDECLARATION_REQUIRED")
        if any(not isinstance(item, str) or item != item.strip() or not item for item in scenario_ids):
            raise ValueError("SLIPPAGE_STRESS_SCENARIO_ID_INVALID")
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("SLIPPAGE_STRESS_SCENARIO_ID_DUPLICATE")

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("SLIPPAGE_STRESS_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("SLIPPAGE_STRESS_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("SLIPPAGE_STRESS_METRIC_DUPLICATE")

        control = self._artifact(control_evidence)
        variant = self._artifact(variant_evidence)
        control_scenarios = self._scenarios_by_id(control)
        variant_scenarios = self._scenarios_by_id(variant)

        if list(control_scenarios) != scenario_ids:
            raise ValueError("SLIPPAGE_STRESS_CONTROL_SCENARIOS_MISMATCH")
        if list(variant_scenarios) != scenario_ids:
            raise ValueError("SLIPPAGE_STRESS_VARIANT_SCENARIOS_MISMATCH")

        comparisons: dict[str, Any] = {}
        for scenario_id in scenario_ids:
            control_item = control_scenarios[scenario_id]
            variant_item = variant_scenarios[scenario_id]
            if control_item["slippage_assumptions"] != variant_item["slippage_assumptions"]:
                raise ValueError(f"SLIPPAGE_STRESS_ASSUMPTIONS_MISMATCH:{scenario_id}")

            compared: dict[str, dict[str, str]] = {}
            for metric in metrics:
                control_value = self._decimal(control_item["metrics"].get(metric), metric)
                variant_value = self._decimal(variant_item["metrics"].get(metric), metric)
                compared[metric] = {
                    "control": self._canonical_decimal(control_value),
                    "variant": self._canonical_decimal(variant_value),
                    "delta": self._canonical_decimal(variant_value - control_value),
                }

            comparisons[scenario_id] = {
                "slippage_assumptions": control_item["slippage_assumptions"],
                "metrics": compared,
            }

        return {
            "schema": "hope.slippage-stress-evaluation.v1",
            "scenarios": comparisons,
        }

    @staticmethod
    def _artifact(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("schema") != SLIPPAGE_STRESS_EVIDENCE_SCHEMA:
            raise ValueError("SLIPPAGE_STRESS_EVIDENCE_REQUIRED")
        scenarios = value.get("scenarios")
        fingerprint = value.get("result_fingerprint")
        if not isinstance(scenarios, list) or not scenarios or not isinstance(fingerprint, str):
            raise ValueError("SLIPPAGE_STRESS_EVIDENCE_REQUIRED")
        if configuration_hash(scenarios) != fingerprint:
            raise ValueError("SLIPPAGE_STRESS_EVIDENCE_FINGERPRINT_MISMATCH")
        return value

    @staticmethod
    def _scenarios_by_id(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in value["scenarios"]:
            if not isinstance(item, dict):
                raise ValueError("SLIPPAGE_STRESS_SCENARIO_INVALID")
            scenario_id = item.get("scenario_id")
            assumptions = item.get("slippage_assumptions")
            metrics = item.get("metrics")
            if (
                not isinstance(scenario_id, str)
                or scenario_id != scenario_id.strip()
                or not scenario_id
                or not isinstance(assumptions, dict)
                or not assumptions
                or not isinstance(metrics, dict)
            ):
                raise ValueError("SLIPPAGE_STRESS_SCENARIO_INVALID")
            if scenario_id in result:
                raise ValueError("SLIPPAGE_STRESS_SCENARIO_ID_DUPLICATE")
            result[scenario_id] = item
        return result

    @staticmethod
    def _decimal(value: Any, metric: str) -> Decimal:
        if not isinstance(value, str):
            raise ValueError(f"SLIPPAGE_STRESS_METRIC_VALUE_INVALID:{metric}")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"SLIPPAGE_STRESS_METRIC_VALUE_INVALID:{metric}") from exc
        if not parsed.is_finite():
            raise ValueError(f"SLIPPAGE_STRESS_METRIC_VALUE_INVALID:{metric}")
        return parsed

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
