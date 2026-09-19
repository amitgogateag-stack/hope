import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.regime_analysis import (
    REGIME_ANALYSIS_EVIDENCE_SCHEMA,
    RegimeAnalysisStageEvaluator,
)


def _regime(regime_id, pnl, sharpe="1.0"):
    return {
        "regime_id": regime_id,
        "metrics": {
            "total_pnl": str(pnl),
            "sharpe": sharpe,
        },
    }


def _artifact(regimes):
    return {
        "schema": REGIME_ANALYSIS_EVIDENCE_SCHEMA,
        "regimes": regimes,
        "result_fingerprint": configuration_hash(regimes),
    }


def test_regime_analysis_compares_exact_predeclared_regimes_without_verdict():
    control = _artifact([
        _regime("trend", 100),
        _regime("range", -25, "0.2"),
    ])
    variant = _artifact([
        _regime("trend", 140),
        _regime("range", -10, "0.3"),
    ])

    result = RegimeAnalysisStageEvaluator().evaluate(
        stage_protocol={
            "regime_ids": ["trend", "range"],
            "metrics": ["total_pnl", "sharpe"],
        },
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.regime-analysis-evaluation.v1"
    assert result["regimes"]["trend"]["metrics"]["total_pnl"]["delta"] == "40"
    assert result["regimes"]["range"]["metrics"]["total_pnl"]["delta"] == "15"
    assert "winner" not in result
    assert "pass" not in result


def test_regime_analysis_requires_predeclared_regimes_and_metrics():
    evidence = _artifact([_regime("trend", 100)])
    evaluator = RegimeAnalysisStageEvaluator()

    with pytest.raises(ValueError, match="REGIME_ANALYSIS_REGIMES_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="REGIME_ANALYSIS_METRICS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"regime_ids": ["trend"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_regime_analysis_rejects_tampered_evidence():
    evidence = _artifact([_regime("trend", 100)])
    evidence["regimes"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(ValueError, match="REGIME_ANALYSIS_EVIDENCE_FINGERPRINT_MISMATCH"):
        RegimeAnalysisStageEvaluator().evaluate(
            stage_protocol={"regime_ids": ["trend"], "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=_artifact([_regime("trend", 100)]),
        )


def test_regime_analysis_rejects_regime_identity_drift():
    control = _artifact([_regime("trend", 100)])
    variant = _artifact([_regime("range", 100)])

    with pytest.raises(ValueError, match="REGIME_ANALYSIS_VARIANT_REGIMES_MISMATCH"):
        RegimeAnalysisStageEvaluator().evaluate(
            stage_protocol={"regime_ids": ["trend"], "metrics": ["total_pnl"]},
            control_evidence=control,
            variant_evidence=variant,
        )


def test_regime_analysis_rejects_invalid_metric_values():
    bad = _regime("trend", 100)
    bad["metrics"]["total_pnl"] = "NaN"

    with pytest.raises(ValueError, match="REGIME_ANALYSIS_METRIC_VALUE_INVALID:total_pnl"):
        RegimeAnalysisStageEvaluator().evaluate(
            stage_protocol={"regime_ids": ["trend"], "metrics": ["total_pnl"]},
            control_evidence=_artifact([bad]),
            variant_evidence=_artifact([_regime("trend", 100)]),
        )
