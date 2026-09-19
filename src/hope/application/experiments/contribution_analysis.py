from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS


CONTRIBUTION_ANALYSIS_EVIDENCE_SCHEMA = "hope.contribution-analysis-evidence.v1"


class ContributionAnalysisStageEvaluator:
    """Compare predeclared contribution buckets without deriving a verdict."""

    stage = "contribution_analysis"

    def evaluate(
        self,
        *,
        stage_protocol: Mapping[str, Any],
        control_evidence: Any,
        variant_evidence: Any,
    ) -> dict[str, Any]:
        contribution_ids = stage_protocol.get("contribution_ids")
        if not isinstance(contribution_ids, list) or not contribution_ids:
            raise ValueError("CONTRIBUTION_ANALYSIS_PREDECLARATION_REQUIRED")
        if any(
            not isinstance(item, str) or item != item.strip() or not item
            for item in contribution_ids
        ):
            raise ValueError("CONTRIBUTION_ANALYSIS_ID_INVALID")
        if len(set(contribution_ids)) != len(contribution_ids):
            raise ValueError("CONTRIBUTION_ANALYSIS_ID_DUPLICATE")

        metrics = stage_protocol.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("CONTRIBUTION_ANALYSIS_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in metrics):
            raise ValueError("CONTRIBUTION_ANALYSIS_METRIC_INVALID")
        if len(set(metrics)) != len(metrics):
            raise ValueError("CONTRIBUTION_ANALYSIS_METRIC_DUPLICATE")

        control = self._artifact(control_evidence)
        variant = self._artifact(variant_evidence)
        control_items = self._items_by_id(control)
        variant_items = self._items_by_id(variant)

        if list(control_items) != contribution_ids:
            raise ValueError("CONTRIBUTION_ANALYSIS_CONTROL_IDS_MISMATCH")
        if list(variant_items) != contribution_ids:
            raise ValueError("CONTRIBUTION_ANALYSIS_VARIANT_IDS_MISMATCH")

        comparisons: dict[str, Any] = {}
        for contribution_id in contribution_ids:
            control_item = control_items[contribution_id]
            variant_item = variant_items[contribution_id]
            if control_item["contribution_definition"] != variant_item["contribution_definition"]:
                raise ValueError(
                    f"CONTRIBUTION_ANALYSIS_DEFINITION_MISMATCH:{contribution_id}"
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

            comparisons[contribution_id] = {
                "contribution_definition": control_item["contribution_definition"],
                "metrics": compared,
            }

        return {
            "schema": "hope.contribution-analysis-evaluation.v1",
            "contributions": comparisons,
        }

    @staticmethod
    def _artifact(value: Any) -> dict[str, Any]:
        if (
            not isinstance(value, dict)
            or value.get("schema") != CONTRIBUTION_ANALYSIS_EVIDENCE_SCHEMA
        ):
            raise ValueError("CONTRIBUTION_ANALYSIS_EVIDENCE_REQUIRED")
        contributions = value.get("contributions")
        fingerprint = value.get("result_fingerprint")
        if (
            not isinstance(contributions, list)
            or not contributions
            or not isinstance(fingerprint, str)
        ):
            raise ValueError("CONTRIBUTION_ANALYSIS_EVIDENCE_REQUIRED")
        if configuration_hash(contributions) != fingerprint:
            raise ValueError("CONTRIBUTION_ANALYSIS_EVIDENCE_FINGERPRINT_MISMATCH")
        return value

    @staticmethod
    def _items_by_id(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in value["contributions"]:
            if not isinstance(item, dict):
                raise ValueError("CONTRIBUTION_ANALYSIS_ITEM_INVALID")
            contribution_id = item.get("contribution_id")
            definition = item.get("contribution_definition")
            metrics = item.get("metrics")
            if (
                not isinstance(contribution_id, str)
                or contribution_id != contribution_id.strip()
                or not contribution_id
                or not isinstance(definition, dict)
                or not definition
                or not isinstance(metrics, dict)
            ):
                raise ValueError("CONTRIBUTION_ANALYSIS_ITEM_INVALID")
            if contribution_id in result:
                raise ValueError("CONTRIBUTION_ANALYSIS_ID_DUPLICATE")
            result[contribution_id] = item
        return result

    @staticmethod
    def _decimal(value: Any, metric: str) -> Decimal:
        if not isinstance(value, str):
            raise ValueError(f"CONTRIBUTION_ANALYSIS_METRIC_VALUE_INVALID:{metric}")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(
                f"CONTRIBUTION_ANALYSIS_METRIC_VALUE_INVALID:{metric}"
            ) from exc
        if not parsed.is_finite():
            raise ValueError(f"CONTRIBUTION_ANALYSIS_METRIC_VALUE_INVALID:{metric}")
        return parsed

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
