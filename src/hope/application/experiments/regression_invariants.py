from __future__ import annotations

from typing import Any, Mapping

from hope.application.validation.research_invariants import _fingerprint
from hope.domain.validation.invariants import INITIAL_INVARIANTS


KNOWN_INVARIANT_IDS = tuple(item.invariant_id for item in INITIAL_INVARIANTS)


class RegressionInvariantsStageEvaluator:
    """Compare predeclared invariant outcomes from immutable per-run artifacts."""

    stage = "regression_invariants"

    def evaluate(
        self,
        *,
        stage_protocol: Mapping[str, Any],
        control_evidence: Any,
        variant_evidence: Any,
    ) -> dict[str, Any]:
        invariant_ids = stage_protocol.get("invariant_ids")
        if not isinstance(invariant_ids, list) or not invariant_ids:
            raise ValueError("REGRESSION_INVARIANTS_PREDECLARATION_REQUIRED")
        if any(value not in KNOWN_INVARIANT_IDS for value in invariant_ids):
            raise ValueError("REGRESSION_INVARIANT_ID_INVALID")
        if len(set(invariant_ids)) != len(invariant_ids):
            raise ValueError("REGRESSION_INVARIANT_ID_DUPLICATE")

        control = self._artifact(control_evidence)
        variant = self._artifact(variant_evidence)
        control_results = self._results_by_id(control)
        variant_results = self._results_by_id(variant)

        comparisons: dict[str, dict[str, Any]] = {}
        for invariant_id in invariant_ids:
            if invariant_id not in control_results:
                raise ValueError(
                    f"REGRESSION_INVARIANT_CONTROL_RESULT_MISSING:{invariant_id}"
                )
            if invariant_id not in variant_results:
                raise ValueError(
                    f"REGRESSION_INVARIANT_VARIANT_RESULT_MISSING:{invariant_id}"
                )
            control_result = control_results[invariant_id]
            variant_result = variant_results[invariant_id]
            comparisons[invariant_id] = {
                "control_status": control_result["status"],
                "variant_status": variant_result["status"],
                "status_changed": control_result["status"] != variant_result["status"],
                "control_message": control_result["message"],
                "variant_message": variant_result["message"],
            }

        return {
            "schema": "hope.regression-invariants-evaluation.v1",
            "invariants": comparisons,
        }

    @staticmethod
    def _artifact(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("REGRESSION_INVARIANT_ARTIFACT_REQUIRED")
        results = value.get("canonical_results")
        fingerprint = value.get("result_fingerprint")
        if not isinstance(results, list) or not isinstance(fingerprint, str):
            raise ValueError("REGRESSION_INVARIANT_ARTIFACT_REQUIRED")
        if _fingerprint(results) != fingerprint:
            raise ValueError("REGRESSION_INVARIANT_ARTIFACT_FINGERPRINT_MISMATCH")
        return value

    @staticmethod
    def _results_by_id(value: Mapping[str, Any]) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for item in value["canonical_results"]:
            if not isinstance(item, dict):
                raise ValueError("REGRESSION_INVARIANT_RESULT_INVALID")
            invariant_id = item.get("invariant_id")
            status = item.get("status")
            message = item.get("message")
            if (
                not isinstance(invariant_id, str)
                or status not in {"PASS", "FAIL", "SKIP"}
                or not isinstance(message, str)
            ):
                raise ValueError("REGRESSION_INVARIANT_RESULT_INVALID")
            if invariant_id in result:
                raise ValueError("REGRESSION_INVARIANT_RESULT_DUPLICATE")
            result[invariant_id] = {
                "status": status,
                "message": message,
            }
        return result
