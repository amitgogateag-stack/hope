import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.walk_forward import (
    WALK_FORWARD_EVIDENCE_SCHEMA,
    WalkForwardStageEvaluator,
)


def _fold(fold_id, start_day, test_day, pnl):
    return {
        "fold_id": fold_id,
        "train_start": f"2025-01-{start_day:02d}T00:00:00+00:00",
        "train_end": f"2025-02-{test_day:02d}T00:00:00+00:00",
        "test_start": f"2025-02-{test_day:02d}T00:00:00+00:00",
        "test_end": f"2025-02-{test_day + 1:02d}T00:00:00+00:00",
        "metrics": {
            "total_pnl": str(pnl),
            "sharpe": "1.25",
        },
    }


def _artifact(folds):
    return {
        "schema": WALK_FORWARD_EVIDENCE_SCHEMA,
        "folds": folds,
        "result_fingerprint": configuration_hash(folds),
    }


def test_walk_forward_compares_exact_predeclared_folds_without_verdict():
    control = _artifact([
        _fold("fold-1", 1, 1, 100),
        _fold("fold-2", 2, 2, 200),
    ])
    variant = _artifact([
        _fold("fold-1", 1, 1, 125),
        _fold("fold-2", 2, 2, 180),
    ])

    result = WalkForwardStageEvaluator().evaluate(
        stage_protocol={
            "fold_ids": ["fold-1", "fold-2"],
            "metrics": ["total_pnl", "sharpe"],
        },
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.walk-forward-evaluation.v1"
    assert result["folds"]["fold-1"]["metrics"]["total_pnl"]["delta"] == "25"
    assert result["folds"]["fold-2"]["metrics"]["total_pnl"]["delta"] == "-20"
    assert "winner" not in result
    assert "pass" not in result


def test_walk_forward_requires_predeclared_metrics_and_folds():
    evidence = _artifact([_fold("fold-1", 1, 1, 100)])
    evaluator = WalkForwardStageEvaluator()

    with pytest.raises(ValueError, match="WALK_FORWARD_METRICS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"fold_ids": ["fold-1"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="WALK_FORWARD_FOLDS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_walk_forward_rejects_tampered_evidence():
    evidence = _artifact([_fold("fold-1", 1, 1, 100)])
    evidence["folds"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(ValueError, match="WALK_FORWARD_EVIDENCE_FINGERPRINT_MISMATCH"):
        WalkForwardStageEvaluator().evaluate(
            stage_protocol={"fold_ids": ["fold-1"], "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=_artifact([_fold("fold-1", 1, 1, 100)]),
        )


def test_walk_forward_rejects_fold_identity_or_window_drift():
    control = _artifact([_fold("fold-1", 1, 1, 100)])
    variant_wrong_id = _artifact([_fold("fold-X", 1, 1, 100)])
    with pytest.raises(ValueError, match="WALK_FORWARD_VARIANT_FOLDS_MISMATCH"):
        WalkForwardStageEvaluator().evaluate(
            stage_protocol={"fold_ids": ["fold-1"], "metrics": ["total_pnl"]},
            control_evidence=control,
            variant_evidence=variant_wrong_id,
        )

    shifted = _fold("fold-1", 1, 1, 100)
    shifted["test_end"] = "2025-02-03T00:00:00+00:00"
    variant_shifted = _artifact([shifted])
    with pytest.raises(ValueError, match="WALK_FORWARD_FOLD_WINDOW_MISMATCH:fold-1"):
        WalkForwardStageEvaluator().evaluate(
            stage_protocol={"fold_ids": ["fold-1"], "metrics": ["total_pnl"]},
            control_evidence=control,
            variant_evidence=variant_shifted,
        )


def test_walk_forward_rejects_invalid_or_overlapping_windows():
    bad = _fold("fold-1", 1, 1, 100)
    bad["train_end"] = bad["train_start"]
    with pytest.raises(ValueError, match="WALK_FORWARD_FOLD_WINDOW_INVALID:fold-1"):
        WalkForwardStageEvaluator().evaluate(
            stage_protocol={"fold_ids": ["fold-1"], "metrics": ["total_pnl"]},
            control_evidence=_artifact([bad]),
            variant_evidence=_artifact([bad]),
        )
