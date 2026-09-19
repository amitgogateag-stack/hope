import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.contribution_analysis import (
    CONTRIBUTION_ANALYSIS_EVIDENCE_SCHEMA,
    ContributionAnalysisStageEvaluator,
)


def _contribution(contribution_id, definition, pnl, sharpe="1.0"):
    return {
        "contribution_id": contribution_id,
        "contribution_definition": definition,
        "metrics": {"total_pnl": str(pnl), "sharpe": str(sharpe)},
    }


def _artifact(contributions):
    return {
        "schema": CONTRIBUTION_ANALYSIS_EVIDENCE_SCHEMA,
        "contributions": contributions,
        "result_fingerprint": configuration_hash(contributions),
    }


def test_contribution_analysis_compares_predeclared_buckets_without_verdict():
    definitions = [
        {"dimension": "symbol_rank", "bucket": "top_decile"},
        {"dimension": "symbol_rank", "bucket": "remaining"},
    ]
    control = _artifact([
        _contribution("top_decile", definitions[0], 70, "0.7"),
        _contribution("remaining", definitions[1], 30, "0.3"),
    ])
    variant = _artifact([
        _contribution("top_decile", definitions[0], 80, "0.8"),
        _contribution("remaining", definitions[1], 45, "0.4"),
    ])

    result = ContributionAnalysisStageEvaluator().evaluate(
        stage_protocol={
            "contribution_ids": ["top_decile", "remaining"],
            "metrics": ["total_pnl", "sharpe"],
        },
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.contribution-analysis-evaluation.v1"
    assert result["contributions"]["remaining"]["metrics"]["total_pnl"]["delta"] == "15"
    assert result["contributions"]["top_decile"]["contribution_definition"] == definitions[0]
    assert "winner" not in result
    assert "concentrated" not in result
    assert "pass" not in result


def test_contribution_analysis_requires_predeclared_ids_and_metrics():
    evidence = _artifact([
        _contribution("all", {"dimension": "all", "bucket": "all"}, 100)
    ])
    evaluator = ContributionAnalysisStageEvaluator()

    with pytest.raises(ValueError, match="CONTRIBUTION_ANALYSIS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_METRICS_PREDECLARATION_REQUIRED",
    ):
        evaluator.evaluate(
            stage_protocol={"contribution_ids": ["all"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_contribution_analysis_rejects_tampered_evidence():
    evidence = _artifact([
        _contribution("all", {"dimension": "all", "bucket": "all"}, 100)
    ])
    evidence["contributions"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_EVIDENCE_FINGERPRINT_MISMATCH",
    ):
        ContributionAnalysisStageEvaluator().evaluate(
            stage_protocol={"contribution_ids": ["all"], "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=_artifact([
                _contribution("all", {"dimension": "all", "bucket": "all"}, 100)
            ]),
        )


def test_contribution_analysis_rejects_identity_drift():
    control = _artifact([
        _contribution("all", {"dimension": "all", "bucket": "all"}, 100)
    ])
    variant = _artifact([
        _contribution("other", {"dimension": "all", "bucket": "all"}, 100)
    ])

    with pytest.raises(ValueError, match="CONTRIBUTION_ANALYSIS_VARIANT_IDS_MISMATCH"):
        ContributionAnalysisStageEvaluator().evaluate(
            stage_protocol={"contribution_ids": ["all"], "metrics": ["total_pnl"]},
            control_evidence=control,
            variant_evidence=variant,
        )


def test_contribution_analysis_rejects_definition_drift():
    control = _artifact([
        _contribution(
            "top_decile",
            {"dimension": "symbol_rank", "bucket": "top_decile"},
            100,
        )
    ])
    variant = _artifact([
        _contribution(
            "top_decile",
            {"dimension": "trade_rank", "bucket": "top_decile"},
            100,
        )
    ])

    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_DEFINITION_MISMATCH:top_decile",
    ):
        ContributionAnalysisStageEvaluator().evaluate(
            stage_protocol={
                "contribution_ids": ["top_decile"],
                "metrics": ["total_pnl"],
            },
            control_evidence=control,
            variant_evidence=variant,
        )
