from copy import deepcopy

import pytest

from hope.application.experiments.historical_evaluation import (
    HistoricalEvaluationStageEvaluator,
)


def _evidence(*, total_pnl="10", sharpe="1.5"):
    return {
        "schema": "hope.certified-backtest-result.v3",
        "execution_provenance": {},
        "research_provenance": {},
        "backtest": {
            "schema": "hope.backtest-result.v1",
            "result": {
                "metrics": {
                    "initial_equity": "100",
                    "final_equity": "110",
                    "total_pnl": total_pnl,
                    "total_return": "0.1",
                    "max_drawdown": "0.05",
                    "volatility": "0.2",
                    "sharpe": sharpe,
                    "downside_deviation": "0.1",
                    "sortino": "2",
                }
            },
        },
    }


def test_historical_evaluator_compares_only_predeclared_metrics_without_verdict():
    result = HistoricalEvaluationStageEvaluator().evaluate(
        stage_protocol={"metrics": ["total_pnl", "sharpe"]},
        control_evidence=_evidence(total_pnl="10", sharpe="1.5"),
        variant_evidence=_evidence(total_pnl="12.5", sharpe="1.25"),
    )

    assert result == {
        "schema": "hope.historical-evaluation.v1",
        "metrics": {
            "total_pnl": {"control": "10", "variant": "12.5", "delta": "2.5"},
            "sharpe": {"control": "1.5", "variant": "1.25", "delta": "-0.25"},
        },
    }
    assert "winner" not in result
    assert "pass" not in result


def test_historical_evaluator_requires_metric_predeclaration():
    with pytest.raises(
        ValueError,
        match="HISTORICAL_EVALUATION_METRICS_PREDECLARATION_REQUIRED",
    ):
        HistoricalEvaluationStageEvaluator().evaluate(
            stage_protocol={"enabled": True},
            control_evidence=_evidence(),
            variant_evidence=_evidence(),
        )


def test_historical_evaluator_rejects_unstored_or_duplicate_metric():
    evaluator = HistoricalEvaluationStageEvaluator()
    with pytest.raises(ValueError, match="HISTORICAL_EVALUATION_METRIC_INVALID"):
        evaluator.evaluate(
            stage_protocol={"metrics": ["profit_factor"]},
            control_evidence=_evidence(),
            variant_evidence=_evidence(),
        )
    with pytest.raises(ValueError, match="HISTORICAL_EVALUATION_METRIC_DUPLICATE"):
        evaluator.evaluate(
            stage_protocol={"metrics": ["total_pnl", "total_pnl"]},
            control_evidence=_evidence(),
            variant_evidence=_evidence(),
        )


def test_historical_evaluator_requires_certified_backtest_evidence():
    bad = deepcopy(_evidence())
    bad["schema"] = "hope.backtest-result.v1"
    with pytest.raises(
        ValueError,
        match="HISTORICAL_EVALUATION_CERTIFIED_EVIDENCE_REQUIRED",
    ):
        HistoricalEvaluationStageEvaluator().evaluate(
            stage_protocol={"metrics": ["total_pnl"]},
            control_evidence=bad,
            variant_evidence=_evidence(),
        )


def test_historical_evaluator_fails_on_missing_metric_value():
    bad = deepcopy(_evidence())
    del bad["backtest"]["result"]["metrics"]["sharpe"]
    with pytest.raises(
        ValueError,
        match="HISTORICAL_EVALUATION_METRIC_VALUE_INVALID:sharpe",
    ):
        HistoricalEvaluationStageEvaluator().evaluate(
            stage_protocol={"metrics": ["sharpe"]},
            control_evidence=bad,
            variant_evidence=_evidence(),
        )
