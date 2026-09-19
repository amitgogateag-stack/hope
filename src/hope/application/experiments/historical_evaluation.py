from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from hope.application.backtests.certified_evidence import (
    CERTIFIED_BACKTEST_EVIDENCE_SCHEMA,
)
from hope.application.backtests.evidence import BACKTEST_EVIDENCE_SCHEMA


HISTORICAL_METRICS = (
    "initial_equity",
    "final_equity",
    "total_pnl",
    "total_return",
    "max_drawdown",
    "volatility",
    "sharpe",
    "downside_deviation",
    "sortino",
)


class HistoricalEvaluationStageEvaluator:
    """Compare only predeclared metrics already present in certified backtest evidence."""

    stage = "historical_evaluation"

    def evaluate(
        self,
        *,
        stage_protocol: Mapping[str, Any],
        control_evidence: Any,
        variant_evidence: Any,
    ) -> dict[str, Any]:
        requested = stage_protocol.get("metrics")
        if not isinstance(requested, list) or not requested:
            raise ValueError("HISTORICAL_EVALUATION_METRICS_PREDECLARATION_REQUIRED")
        if any(metric not in HISTORICAL_METRICS for metric in requested):
            raise ValueError("HISTORICAL_EVALUATION_METRIC_INVALID")
        if len(set(requested)) != len(requested):
            raise ValueError("HISTORICAL_EVALUATION_METRIC_DUPLICATE")

        control_metrics = self._metrics(control_evidence)
        variant_metrics = self._metrics(variant_evidence)
        comparisons: dict[str, dict[str, str]] = {}
        for metric in requested:
            control_value = self._decimal(control_metrics.get(metric), metric)
            variant_value = self._decimal(variant_metrics.get(metric), metric)
            comparisons[metric] = {
                "control": self._canonical_decimal(control_value),
                "variant": self._canonical_decimal(variant_value),
                "delta": self._canonical_decimal(variant_value - control_value),
            }

        return {
            "schema": "hope.historical-evaluation.v1",
            "metrics": comparisons,
        }

    @staticmethod
    def _metrics(evidence: Any) -> Mapping[str, Any]:
        if not isinstance(evidence, dict):
            raise ValueError("HISTORICAL_EVALUATION_CERTIFIED_EVIDENCE_REQUIRED")
        if evidence.get("schema") != CERTIFIED_BACKTEST_EVIDENCE_SCHEMA:
            raise ValueError("HISTORICAL_EVALUATION_CERTIFIED_EVIDENCE_REQUIRED")
        backtest = evidence.get("backtest")
        if not isinstance(backtest, dict) or backtest.get("schema") != BACKTEST_EVIDENCE_SCHEMA:
            raise ValueError("HISTORICAL_EVALUATION_BACKTEST_EVIDENCE_REQUIRED")
        result = backtest.get("result")
        if not isinstance(result, dict):
            raise ValueError("HISTORICAL_EVALUATION_BACKTEST_RESULT_REQUIRED")
        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            raise ValueError("HISTORICAL_EVALUATION_METRICS_REQUIRED")
        return metrics

    @staticmethod
    def _decimal(value: Any, metric: str) -> Decimal:
        if not isinstance(value, str):
            raise ValueError(f"HISTORICAL_EVALUATION_METRIC_VALUE_INVALID:{metric}")
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(
                f"HISTORICAL_EVALUATION_METRIC_VALUE_INVALID:{metric}"
            ) from exc
        if not parsed.is_finite():
            raise ValueError(f"HISTORICAL_EVALUATION_METRIC_VALUE_INVALID:{metric}")
        return parsed

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
