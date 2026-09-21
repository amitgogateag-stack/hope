from copy import deepcopy
from uuid import uuid4

import pytest

from hope.application.experiments.historical_evaluation import (
    HistoricalEvaluationStageEvaluator,
)


_CONTEXT = {
    "dataset_version_id": str(uuid4()),
    "universe_version_id": str(uuid4()),
    "universe_membership_hash": "a" * 64,
    "market_data_manifest_hash": "b" * 64,
    "as_of": "2026-08-31T00:00:00+00:00",
}


def _evidence(*, total_pnl="10", sharpe="1.5", context=None):
    research_context = dict(_CONTEXT if context is None else context)
    return {
        "schema": "hope.certified-backtest-result.v3",
        "execution_provenance": {},
        "research_provenance": {
            "experiment_id": "exp-1",
            "strategy_version_id": str(uuid4()),
            "configuration_hash": "c" * 64,
            "environment": "BACKTEST",
            "run_fingerprint": "d" * 64,
            **research_context,
        },
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


def _protocol(metrics=("total_pnl", "sharpe"), context=None):
    return {
        "metrics": list(metrics),
        "evaluation_context": dict(_CONTEXT if context is None else context),
    }


def test_historical_evaluator_compares_only_predeclared_metrics_in_predeclared_context():
    result = HistoricalEvaluationStageEvaluator().evaluate(
        stage_protocol=_protocol(),
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


def test_historical_evaluator_requires_metric_and_context_predeclaration():
    evaluator = HistoricalEvaluationStageEvaluator()
    with pytest.raises(
        ValueError,
        match="HISTORICAL_EVALUATION_METRICS_PREDECLARATION_REQUIRED",
    ):
        evaluator.evaluate(
            stage_protocol={"evaluation_context": _CONTEXT},
            control_evidence=_evidence(),
            variant_evidence=_evidence(),
        )

    with pytest.raises(
        ValueError,
        match="HISTORICAL_EVALUATION_CONTEXT_PREDECLARATION_REQUIRED",
    ):
        evaluator.evaluate(
            stage_protocol={"metrics": ["total_pnl"]},
            control_evidence=_evidence(),
            variant_evidence=_evidence(),
        )


def test_historical_evaluator_rejects_context_drift():
    drifted = dict(_CONTEXT)
    drifted["market_data_manifest_hash"] = "e" * 64
    with pytest.raises(
        ValueError,
        match="HISTORICAL_EVALUATION_PREDECLARED_CONTEXT_MISMATCH",
    ):
        HistoricalEvaluationStageEvaluator().evaluate(
            stage_protocol=_protocol(("total_pnl",)),
            control_evidence=_evidence(),
            variant_evidence=_evidence(context=drifted),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_version_id", "not-a-uuid"),
        ("universe_version_id", " NOT-A-UUID "),
        ("universe_membership_hash", "A" * 64),
        ("market_data_manifest_hash", "b" * 63),
        ("as_of", "2026-08-31T00:00:00"),
    ],
)
def test_historical_evaluator_rejects_noncanonical_predeclared_context(field, value):
    context = dict(_CONTEXT)
    context[field] = value

    with pytest.raises(ValueError, match="HISTORICAL_EVALUATION_CONTEXT_INVALID"):
        HistoricalEvaluationStageEvaluator().evaluate(
            stage_protocol=_protocol(("total_pnl",), context),
            control_evidence=_evidence(),
            variant_evidence=_evidence(),
        )


def test_historical_evaluator_rejects_noncanonical_evidence_context():
    context = dict(_CONTEXT)
    context["market_data_manifest_hash"] = " B" * 32

    with pytest.raises(
        ValueError,
        match="HISTORICAL_EVALUATION_RESEARCH_PROVENANCE_NOT_CANONICAL",
    ):
        HistoricalEvaluationStageEvaluator().evaluate(
            stage_protocol=_protocol(("total_pnl",)),
            control_evidence=_evidence(context=context),
            variant_evidence=_evidence(),
        )


def test_historical_evaluator_rejects_unstored_or_duplicate_metric():
    evaluator = HistoricalEvaluationStageEvaluator()
    with pytest.raises(ValueError, match="HISTORICAL_EVALUATION_METRIC_INVALID"):
        evaluator.evaluate(
            stage_protocol=_protocol(("profit_factor",)),
            control_evidence=_evidence(),
            variant_evidence=_evidence(),
        )
    with pytest.raises(ValueError, match="HISTORICAL_EVALUATION_METRIC_DUPLICATE"):
        evaluator.evaluate(
            stage_protocol=_protocol(("total_pnl", "total_pnl")),
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
            stage_protocol=_protocol(("total_pnl",)),
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
            stage_protocol=_protocol(("sharpe",)),
            control_evidence=bad,
            variant_evidence=_evidence(),
        )
