from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.historical_evaluation import HISTORICAL_METRICS


WALK_FORWARD_EVIDENCE_SCHEMA = "hope.walk-forward-evidence.v1"


class WalkForwardStageEvaluator:
    """Compare predeclared out-of-sample fold metrics without producing a verdict."""

    stage = "walk_forward"

    def evaluate(
        self,
        *,
        stage_protocol: Mapping[str, Any],
        control_evidence: Any,
        variant_evidence: Any,
    ) -> dict[str, Any]:
        requested_metrics = stage_protocol.get("metrics")
        if not isinstance(requested_metrics, list) or not requested_metrics:
            raise ValueError("WALK_FORWARD_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in requested_metrics):
            raise ValueError("WALK_FORWARD_METRIC_INVALID")
        if len(set(requested_metrics)) != len(requested_metrics):
            raise ValueError("WALK_FORWARD_METRIC_DUPLICATE")

        requested_folds = stage_protocol.get("fold_ids")
        if not isinstance(requested_folds, list) or not requested_folds:
            raise ValueError("WALK_FORWARD_FOLDS_PREDECLARATION_REQUIRED")
        if any(not isinstance(fold_id, str) or not fold_id.strip() for fold_id in requested_folds):
            raise ValueError("WALK_FORWARD_FOLD_ID_INVALID")
        if len(set(requested_folds)) != len(requested_folds):
            raise ValueError("WALK_FORWARD_FOLD_ID_DUPLICATE")

        control = self._artifact(control_evidence)
        variant = self._artifact(variant_evidence)
        control_folds = self._folds_by_id(control)
        variant_folds = self._folds_by_id(variant)

        if list(control_folds) != requested_folds:
            raise ValueError("WALK_FORWARD_CONTROL_FOLDS_MISMATCH")
        if list(variant_folds) != requested_folds:
            raise ValueError("WALK_FORWARD_VARIANT_FOLDS_MISMATCH")

        comparisons: dict[str, Any] = {}
        for fold_id in requested_folds:
            control_fold = control_folds[fold_id]
            variant_fold = variant_folds[fold_id]
            if self._window(control_fold) != self._window(variant_fold):
                raise ValueError(f"WALK_FORWARD_FOLD_WINDOW_MISMATCH:{fold_id}")

            metrics: dict[str, dict[str, str]] = {}
            for metric in requested_metrics:
                control_value = self._decimal(control_fold["metrics"].get(metric), metric)
                variant_value = self._decimal(variant_fold["metrics"].get(metric), metric)
                metrics[metric] = {
                    "control": self._canonical_decimal(control_value),
                    "variant": self._canonical_decimal(variant_value),
                    "delta": self._canonical_decimal(variant_value - control_value),
                }
            comparisons[fold_id] = {
                "window": self._window(control_fold),
                "metrics": metrics,
            }

        return {
            "schema": "hope.walk-forward-evaluation.v1",
            "folds": comparisons,
        }

    @staticmethod
    def _artifact(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("schema") != WALK_FORWARD_EVIDENCE_SCHEMA:
            raise ValueError("WALK_FORWARD_EVIDENCE_REQUIRED")
        folds = value.get("folds")
        fingerprint = value.get("result_fingerprint")
        if not isinstance(folds, list) or not folds or not isinstance(fingerprint, str):
            raise ValueError("WALK_FORWARD_EVIDENCE_REQUIRED")
        if configuration_hash(folds) != fingerprint:
            raise ValueError("WALK_FORWARD_EVIDENCE_FINGERPRINT_MISMATCH")
        return value

    @classmethod
    def _folds_by_id(cls, value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        previous_test_end: datetime | None = None
        for fold in value["folds"]:
            if not isinstance(fold, dict):
                raise ValueError("WALK_FORWARD_FOLD_INVALID")
            fold_id = fold.get("fold_id")
            metrics = fold.get("metrics")
            if not isinstance(fold_id, str) or not fold_id.strip() or not isinstance(metrics, dict):
                raise ValueError("WALK_FORWARD_FOLD_INVALID")
            if fold_id in result:
                raise ValueError("WALK_FORWARD_FOLD_ID_DUPLICATE")

            train_start = cls._time(fold.get("train_start"))
            train_end = cls._time(fold.get("train_end"))
            test_start = cls._time(fold.get("test_start"))
            test_end = cls._time(fold.get("test_end"))
            if not (train_start < train_end <= test_start < test_end):
                raise ValueError(f"WALK_FORWARD_FOLD_WINDOW_INVALID:{fold_id}")
            if previous_test_end is not None and test_start < previous_test_end:
                raise ValueError("WALK_FORWARD_TEST_WINDOWS_OVERLAP")
            previous_test_end = test_end
            result[fold_id] = fold
        return result

    @staticmethod
    def _time(value: Any) -> datetime:
        if not isinstance(value, str):
            raise ValueError("WALK_FORWARD_TIME_INVALID")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("WALK_FORWARD_TIME_INVALID") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.isoformat() != value:
            raise ValueError("WALK_FORWARD_TIME_INVALID")
        return parsed

    @staticmethod
    def _window(fold: Mapping[str, Any]) -> dict[str, str]:
        return {
            "train_start": fold["train_start"],
            "train_end": fold["train_end"],
            "test_start": fold["test_start"],
            "test_end": fold["test_end"],
        }

    @staticmethod
    def _decimal(value: Any, metric: str) -> Decimal:
        if not isinstance(value, str):
            raise ValueError(f"WALK_FORWARD_METRIC_VALUE_INVALID:{metric}")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"WALK_FORWARD_METRIC_VALUE_INVALID:{metric}") from exc
        if not parsed.is_finite():
            raise ValueError(f"WALK_FORWARD_METRIC_VALUE_INVALID:{metric}")
        return parsed

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
