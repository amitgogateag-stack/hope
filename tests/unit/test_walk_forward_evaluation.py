import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.walk_forward import (
    WALK_FORWARD_EVIDENCE_SCHEMA,
    WalkForwardStageEvaluator,
)


def _fold(fold_id, window, pnl):
    return {
        "fold_id": fold_id,
        **window,
        "metrics": {"total_pnl": str(pnl), "sharpe": "1.25"},
    }


def _artifact(folds):
    return {
        "schema": WALK_FORWARD_EVIDENCE_SCHEMA,
        "folds": folds,
        "result_fingerprint": configuration_hash(folds),
    }


def _protocol(ids, definitions, metrics=("total_pnl", "sharpe")):
    return {
        "fold_ids": ids,
        "fold_definitions": definitions,
        "metrics": list(metrics),
    }


def test_walk_forward_compares_exact_predeclared_folds_without_verdict():
    definitions = {
        "fold-1": {
            "train_start": "2025-01-01T00:00:00+00:00",
            "train_end": "2025-02-01T00:00:00+00:00",
            "test_start": "2025-02-01T00:00:00+00:00",
            "test_end": "2025-02-02T00:00:00+00:00",
        },
        "fold-2": {
            "train_start": "2025-01-02T00:00:00+00:00",
            "train_end": "2025-02-02T00:00:00+00:00",
            "test_start": "2025-02-02T00:00:00+00:00",
            "test_end": "2025-02-03T00:00:00+00:00",
        },
    }
    control = _artifact([
        _fold("fold-1", definitions["fold-1"], 100),
        _fold("fold-2", definitions["fold-2"], 200),
    ])
    variant = _artifact([
        _fold("fold-1", definitions["fold-1"], 125),
        _fold("fold-2", definitions["fold-2"], 180),
    ])

    result = WalkForwardStageEvaluator().evaluate(
        stage_protocol=_protocol(["fold-1", "fold-2"], definitions),
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.walk-forward-evaluation.v1"
    assert result["folds"]["fold-1"]["metrics"]["total_pnl"]["delta"] == "25"
    assert result["folds"]["fold-1"]["window"] == definitions["fold-1"]
    assert "winner" not in result
    assert "pass" not in result


def test_walk_forward_requires_predeclared_metrics_folds_and_windows():
    definition = {
        "train_start": "2025-01-01T00:00:00+00:00",
        "train_end": "2025-02-01T00:00:00+00:00",
        "test_start": "2025-02-01T00:00:00+00:00",
        "test_end": "2025-02-02T00:00:00+00:00",
    }
    evidence = _artifact([_fold("fold-1", definition, 100)])
    evaluator = WalkForwardStageEvaluator()

    with pytest.raises(ValueError, match="WALK_FORWARD_FOLDS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"fold_definitions": {"fold-1": definition}, "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="WALK_FORWARD_FOLD_DEFINITIONS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"fold_ids": ["fold-1"], "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="WALK_FORWARD_METRICS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"fold_ids": ["fold-1"], "fold_definitions": {"fold-1": definition}},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_walk_forward_rejects_noncanonical_protocol_fold_id():
    definition = {
        "train_start": "2025-01-01T00:00:00+00:00",
        "train_end": "2025-02-01T00:00:00+00:00",
        "test_start": "2025-02-01T00:00:00+00:00",
        "test_end": "2025-02-02T00:00:00+00:00",
    }
    evidence = _artifact([_fold(" fold-1", definition, 100)])

    with pytest.raises(ValueError, match="WALK_FORWARD_FOLD_ID_INVALID"):
        WalkForwardStageEvaluator().evaluate(
            stage_protocol=_protocol([" fold-1"], {" fold-1": definition}),
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_walk_forward_rejects_noncanonical_evidence_fold_id():
    definition = {
        "train_start": "2025-01-01T00:00:00+00:00",
        "train_end": "2025-02-01T00:00:00+00:00",
        "test_start": "2025-02-01T00:00:00+00:00",
        "test_end": "2025-02-02T00:00:00+00:00",
    }
    canonical = _artifact([_fold("fold-1", definition, 100)])
    noncanonical = _artifact([_fold(" fold-1", definition, 100)])

    with pytest.raises(ValueError, match="WALK_FORWARD_FOLD_INVALID"):
        WalkForwardStageEvaluator().evaluate(
            stage_protocol=_protocol(["fold-1"], {"fold-1": definition}),
            control_evidence=noncanonical,
            variant_evidence=canonical,
        )


def test_walk_forward_rejects_predeclared_window_drift():
    definition = {
        "train_start": "2025-01-01T00:00:00+00:00",
        "train_end": "2025-02-01T00:00:00+00:00",
        "test_start": "2025-02-01T00:00:00+00:00",
        "test_end": "2025-02-02T00:00:00+00:00",
    }
    shifted = dict(definition)
    shifted["test_end"] = "2025-02-03T00:00:00+00:00"

    with pytest.raises(ValueError, match="WALK_FORWARD_PREDECLARED_WINDOW_MISMATCH:fold-1"):
        WalkForwardStageEvaluator().evaluate(
            stage_protocol=_protocol(["fold-1"], {"fold-1": definition}, ("total_pnl",)),
            control_evidence=_artifact([_fold("fold-1", definition, 100)]),
            variant_evidence=_artifact([_fold("fold-1", shifted, 100)]),
        )


def test_walk_forward_rejects_tampered_evidence():
    definition = {
        "train_start": "2025-01-01T00:00:00+00:00",
        "train_end": "2025-02-01T00:00:00+00:00",
        "test_start": "2025-02-01T00:00:00+00:00",
        "test_end": "2025-02-02T00:00:00+00:00",
    }
    evidence = _artifact([_fold("fold-1", definition, 100)])
    evidence["folds"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(ValueError, match="WALK_FORWARD_EVIDENCE_FINGERPRINT_MISMATCH"):
        WalkForwardStageEvaluator().evaluate(
            stage_protocol=_protocol(["fold-1"], {"fold-1": definition}, ("total_pnl",)),
            control_evidence=evidence,
            variant_evidence=_artifact([_fold("fold-1", definition, 100)]),
        )
