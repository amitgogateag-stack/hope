import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.regime_analysis import (
    REGIME_ANALYSIS_EVIDENCE_SCHEMA,
    RegimeAnalysisStageEvaluator,
)


def _regime(regime_id, definition, pnl, sharpe="1.0"):
    return {
        "regime_id": regime_id,
        "regime_definition": definition,
        "metrics": {"total_pnl": str(pnl), "sharpe": sharpe},
    }


def _artifact(regimes):
    return {
        "schema": REGIME_ANALYSIS_EVIDENCE_SCHEMA,
        "regimes": regimes,
        "result_fingerprint": configuration_hash(regimes),
    }


def _protocol(ids, definitions, metrics=("total_pnl", "sharpe")):
    return {
        "regime_ids": ids,
        "regime_definitions": definitions,
        "metrics": list(metrics),
    }


def test_regime_analysis_compares_exact_predeclared_regimes_without_verdict():
    definitions = {
        "trend": {"classifier": "adx", "operator": ">=", "threshold": "25"},
        "range": {"classifier": "adx", "operator": "<", "threshold": "25"},
    }
    control = _artifact([
        _regime("trend", definitions["trend"], 100),
        _regime("range", definitions["range"], -25, "0.2"),
    ])
    variant = _artifact([
        _regime("trend", definitions["trend"], 140),
        _regime("range", definitions["range"], -10, "0.3"),
    ])

    result = RegimeAnalysisStageEvaluator().evaluate(
        stage_protocol=_protocol(["trend", "range"], definitions),
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.regime-analysis-evaluation.v1"
    assert result["regimes"]["trend"]["metrics"]["total_pnl"]["delta"] == "40"
    assert result["regimes"]["trend"]["regime_definition"] == definitions["trend"]
    assert "winner" not in result
    assert "pass" not in result


def test_regime_analysis_requires_predeclared_regimes_definitions_and_metrics():
    definitions = {"trend": {"classifier": "adx", "operator": ">=", "threshold": "25"}}
    evidence = _artifact([_regime("trend", definitions["trend"], 100)])
    evaluator = RegimeAnalysisStageEvaluator()

    with pytest.raises(ValueError, match="REGIME_ANALYSIS_REGIMES_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"regime_definitions": definitions, "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="REGIME_ANALYSIS_DEFINITIONS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"regime_ids": ["trend"], "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="REGIME_ANALYSIS_METRICS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"regime_ids": ["trend"], "regime_definitions": definitions},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_regime_analysis_rejects_definition_drift():
    definitions = {"trend": {"classifier": "adx", "operator": ">=", "threshold": "25"}}
    control = _artifact([_regime("trend", definitions["trend"], 100)])
    variant = _artifact([
        _regime(
            "trend",
            {"classifier": "adx", "operator": ">=", "threshold": "30"},
            100,
        )
    ])

    with pytest.raises(
        ValueError,
        match="REGIME_ANALYSIS_PREDECLARED_DEFINITION_MISMATCH:trend",
    ):
        RegimeAnalysisStageEvaluator().evaluate(
            stage_protocol=_protocol(["trend"], definitions, ("total_pnl",)),
            control_evidence=control,
            variant_evidence=variant,
        )


def test_regime_analysis_rejects_tampered_evidence():
    definitions = {"trend": {"classifier": "adx", "operator": ">=", "threshold": "25"}}
    evidence = _artifact([_regime("trend", definitions["trend"], 100)])
    evidence["regimes"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(ValueError, match="REGIME_ANALYSIS_EVIDENCE_FINGERPRINT_MISMATCH"):
        RegimeAnalysisStageEvaluator().evaluate(
            stage_protocol=_protocol(["trend"], definitions, ("total_pnl",)),
            control_evidence=evidence,
            variant_evidence=_artifact([_regime("trend", definitions["trend"], 100)]),
        )
