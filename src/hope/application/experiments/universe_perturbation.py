from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS


UNIVERSE_PERTURBATION_EVIDENCE_SCHEMA = "hope.universe-perturbation-evidence.v1"


class UniversePerturbationStageEvaluator:
    """Compare predeclared universe perturbations without deriving a verdict."""

    stage = "universe_perturbation"

    def evaluate(
        self,
        *,
        stage_protocol: Mapping[str, Any],
        control_evidence: Any,
        variant_evidence: Any,
    ) -> dict[str, Any]:
        perturbation_ids = stage_protocol.get("perturbation_ids")
        if not isinstance(perturbation_ids, list) or not perturbation_ids:
            raise ValueError("UNIVERSE_PERTURBATION_PREDECLARATION_REQUIRED")
        if any(
            not isinstance(item, str) or item != item.strip() or not item
            for item in perturbation_ids
        ):
            raise ValueError("UNIVERSE_PERTURBATION_ID_INVALID")
        if len(set(perturbation_ids)) != len(perturbation_ids):
            raise ValueError("UNIVERSE_PERTURBATION_ID_DUPLICATE")

        definitions = stage_protocol.get("perturbation_definitions")
        if not isinstance(definitions, dict) or set(definitions) != set(perturbation_ids):
            raise ValueError("UNIVERSE_PERTURBATION_DEFINITIONS_PREDECLARATION_REQUIRED")
        for perturbation_id in perturbation_ids:
            definition = definitions.get(perturbation_id)
            if not isinstance(definition, dict) or not definition:
                raise ValueError(
                    f"UNIVERSE_PERTURBATION_DEFINITION_INVALID:{perturbation_id}"
                )

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("UNIVERSE_PERTURBATION_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("UNIVERSE_PERTURBATION_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("UNIVERSE_PERTURBATION_METRIC_DUPLICATE")

        control = self._artifact(control_evidence)
        variant = self._artifact(variant_evidence)
        control_items = self._items_by_id(control)
        variant_items = self._items_by_id(variant)

        if list(control_items) != perturbation_ids:
            raise ValueError("UNIVERSE_PERTURBATION_CONTROL_IDS_MISMATCH")
        if list(variant_items) != perturbation_ids:
            raise ValueError("UNIVERSE_PERTURBATION_VARIANT_IDS_MISMATCH")

        comparisons: dict[str, Any] = {}
        for perturbation_id in perturbation_ids:
            control_item = control_items[perturbation_id]
            variant_item = variant_items[perturbation_id]
            expected = definitions[perturbation_id]
            if (
                control_item["universe_definition"] != expected
                or variant_item["universe_definition"] != expected
            ):
                raise ValueError(
                    f"UNIVERSE_PERTURBATION_PREDECLARED_DEFINITION_MISMATCH:{perturbation_id}"
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

            comparisons[perturbation_id] = {
                "universe_definition": expected,
                "metrics": compared,
            }

        return {
            "schema": "hope.universe-perturbation-evaluation.v1",
            "perturbations": comparisons,
        }

    @staticmethod
    def _artifact(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("schema") != UNIVERSE_PERTURBATION_EVIDENCE_SCHEMA:
            raise ValueError("UNIVERSE_PERTURBATION_EVIDENCE_REQUIRED")
        perturbations = value.get("perturbations")
        fingerprint = value.get("result_fingerprint")
        if (
            not isinstance(perturbations, list)
            or not perturbations
            or not isinstance(fingerprint, str)
        ):
            raise ValueError("UNIVERSE_PERTURBATION_EVIDENCE_REQUIRED")
        if configuration_hash(perturbations) != fingerprint:
            raise ValueError("UNIVERSE_PERTURBATION_EVIDENCE_FINGERPRINT_MISMATCH")
        return value

    @staticmethod
    def _items_by_id(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in value["perturbations"]:
            if not isinstance(item, dict):
                raise ValueError("UNIVERSE_PERTURBATION_ITEM_INVALID")
            perturbation_id = item.get("perturbation_id")
            universe_definition = item.get("universe_definition")
            metrics = item.get("metrics")
            if (
                not isinstance(perturbation_id, str)
                or perturbation_id != perturbation_id.strip()
                or not perturbation_id
                or not isinstance(universe_definition, dict)
                or not universe_definition
                or not isinstance(metrics, dict)
            ):
                raise ValueError("UNIVERSE_PERTURBATION_ITEM_INVALID")
            if perturbation_id in result:
                raise ValueError("UNIVERSE_PERTURBATION_ID_DUPLICATE")
            result[perturbation_id] = item
        return result

    @staticmethod
    def _decimal(value: Any, metric: str) -> Decimal:
        if not isinstance(value, str):
            raise ValueError(f"UNIVERSE_PERTURBATION_METRIC_VALUE_INVALID:{metric}")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(
                f"UNIVERSE_PERTURBATION_METRIC_VALUE_INVALID:{metric}"
            ) from exc
        if not parsed.is_finite():
            raise ValueError(f"UNIVERSE_PERTURBATION_METRIC_VALUE_INVALID:{metric}")
        return parsed

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
