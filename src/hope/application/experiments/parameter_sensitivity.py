from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS


PARAMETER_SENSITIVITY_EVIDENCE_SCHEMA = "hope.parameter-sensitivity-evidence.v1"


class ParameterSensitivityStageEvaluator:
    """Compare predeclared parameter sets without choosing an optimum."""

    stage = "parameter_sensitivity"

    def evaluate(
        self,
        *,
        stage_protocol: Mapping[str, Any],
        control_evidence: Any,
        variant_evidence: Any,
    ) -> dict[str, Any]:
        parameter_set_ids = stage_protocol.get("parameter_set_ids")
        if not isinstance(parameter_set_ids, list) or not parameter_set_ids:
            raise ValueError("PARAMETER_SENSITIVITY_PARAMETER_SETS_PREDECLARATION_REQUIRED")
        if any(not isinstance(item, str) or item != item.strip() or not item for item in parameter_set_ids):
            raise ValueError("PARAMETER_SENSITIVITY_PARAMETER_SET_ID_INVALID")
        if len(set(parameter_set_ids)) != len(parameter_set_ids):
            raise ValueError("PARAMETER_SENSITIVITY_PARAMETER_SET_ID_DUPLICATE")

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("PARAMETER_SENSITIVITY_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("PARAMETER_SENSITIVITY_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("PARAMETER_SENSITIVITY_METRIC_DUPLICATE")

        control = self._artifact(control_evidence)
        variant = self._artifact(variant_evidence)
        control_sets = self._sets_by_id(control)
        variant_sets = self._sets_by_id(variant)

        if list(control_sets) != parameter_set_ids:
            raise ValueError("PARAMETER_SENSITIVITY_CONTROL_PARAMETER_SETS_MISMATCH")
        if list(variant_sets) != parameter_set_ids:
            raise ValueError("PARAMETER_SENSITIVITY_VARIANT_PARAMETER_SETS_MISMATCH")

        comparisons: dict[str, Any] = {}
        for parameter_set_id in parameter_set_ids:
            control_item = control_sets[parameter_set_id]
            variant_item = variant_sets[parameter_set_id]
            if control_item["parameters"] != variant_item["parameters"]:
                raise ValueError(
                    f"PARAMETER_SENSITIVITY_PARAMETER_DEFINITION_MISMATCH:{parameter_set_id}"
                )

            compared: dict[str, dict[str, str]] = {}
            for metric in metrics:
                control_value = self._decimal(control_item["metrics"].get(metric), metric)
                variant_value = self._decimal(variant_item["metrics"].get(metric), metric)
                compared[metric] = {
                    "control": self._canonical_decimal(control_value),
                    "variant": self._canonical_decimal(variant_value),
                    "delta": self._canonical_decimal(variant_value - control_value),
                }

            comparisons[parameter_set_id] = {
                "parameters": control_item["parameters"],
                "metrics": compared,
            }

        return {
            "schema": "hope.parameter-sensitivity-evaluation.v1",
            "parameter_sets": comparisons,
        }

    @staticmethod
    def _artifact(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("schema") != PARAMETER_SENSITIVITY_EVIDENCE_SCHEMA:
            raise ValueError("PARAMETER_SENSITIVITY_EVIDENCE_REQUIRED")
        parameter_sets = value.get("parameter_sets")
        fingerprint = value.get("result_fingerprint")
        if not isinstance(parameter_sets, list) or not parameter_sets or not isinstance(fingerprint, str):
            raise ValueError("PARAMETER_SENSITIVITY_EVIDENCE_REQUIRED")
        if configuration_hash(parameter_sets) != fingerprint:
            raise ValueError("PARAMETER_SENSITIVITY_EVIDENCE_FINGERPRINT_MISMATCH")
        return value

    @staticmethod
    def _sets_by_id(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in value["parameter_sets"]:
            if not isinstance(item, dict):
                raise ValueError("PARAMETER_SENSITIVITY_PARAMETER_SET_INVALID")
            parameter_set_id = item.get("parameter_set_id")
            parameters = item.get("parameters")
            metrics = item.get("metrics")
            if (
                not isinstance(parameter_set_id, str)
                or parameter_set_id != parameter_set_id.strip()
                or not parameter_set_id
                or not isinstance(parameters, dict)
                or not isinstance(metrics, dict)
            ):
                raise ValueError("PARAMETER_SENSITIVITY_PARAMETER_SET_INVALID")
            if parameter_set_id in result:
                raise ValueError("PARAMETER_SENSITIVITY_PARAMETER_SET_ID_DUPLICATE")
            result[parameter_set_id] = item
        return result

    @staticmethod
    def _decimal(value: Any, metric: str) -> Decimal:
        if not isinstance(value, str):
            raise ValueError(f"PARAMETER_SENSITIVITY_METRIC_VALUE_INVALID:{metric}")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"PARAMETER_SENSITIVITY_METRIC_VALUE_INVALID:{metric}") from exc
        if not parsed.is_finite():
            raise ValueError(f"PARAMETER_SENSITIVITY_METRIC_VALUE_INVALID:{metric}")
        return parsed

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
