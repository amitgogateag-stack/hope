from uuid import uuid4

import pytest

from hope.application.experiments.regression_invariants import (
    RegressionInvariantsStageEvaluator,
)
from hope.application.validation.research_invariants import _fingerprint


def _artifact(status_a="PASS", status_b="PASS"):
    results = [
        {
            "invariant_id": "INVARIANT-001",
            "status": status_a,
            "message": "member count check",
        },
        {
            "invariant_id": "INVARIANT-002",
            "status": status_b,
            "message": "identity check",
        },
    ]
    return {
        "research_run_id": uuid4(),
        "context_fingerprint": "a" * 64,
        "result_fingerprint": _fingerprint(results),
        "canonical_results": results,
    }


def test_regression_invariants_compares_predeclared_exact_statuses_without_verdict():
    result = RegressionInvariantsStageEvaluator().evaluate(
        stage_protocol={"invariant_ids": ["INVARIANT-001", "INVARIANT-002"]},
        control_evidence=_artifact("PASS", "PASS"),
        variant_evidence=_artifact("PASS", "FAIL"),
    )

    assert result["schema"] == "hope.regression-invariants-evaluation.v1"
    assert result["invariants"]["INVARIANT-001"]["status_changed"] is False
    assert result["invariants"]["INVARIANT-002"] == {
        "control_status": "PASS",
        "variant_status": "FAIL",
        "status_changed": True,
        "control_message": "identity check",
        "variant_message": "identity check",
    }
    assert "winner" not in result
    assert "pass" not in result


def test_regression_invariants_requires_predeclared_ids():
    with pytest.raises(
        ValueError,
        match="REGRESSION_INVARIANTS_PREDECLARATION_REQUIRED",
    ):
        RegressionInvariantsStageEvaluator().evaluate(
            stage_protocol={"enabled": True},
            control_evidence=_artifact(),
            variant_evidence=_artifact(),
        )


def test_regression_invariants_rejects_unknown_and_duplicate_ids():
    evaluator = RegressionInvariantsStageEvaluator()
    with pytest.raises(ValueError, match="REGRESSION_INVARIANT_ID_INVALID"):
        evaluator.evaluate(
            stage_protocol={"invariant_ids": ["INVARIANT-999"]},
            control_evidence=_artifact(),
            variant_evidence=_artifact(),
        )
    with pytest.raises(ValueError, match="REGRESSION_INVARIANT_ID_DUPLICATE"):
        evaluator.evaluate(
            stage_protocol={"invariant_ids": ["INVARIANT-001", "INVARIANT-001"]},
            control_evidence=_artifact(),
            variant_evidence=_artifact(),
        )


def test_regression_invariants_rejects_tampered_artifact():
    bad = _artifact()
    bad["result_fingerprint"] = "0" * 64
    with pytest.raises(
        ValueError,
        match="REGRESSION_INVARIANT_ARTIFACT_FINGERPRINT_MISMATCH",
    ):
        RegressionInvariantsStageEvaluator().evaluate(
            stage_protocol={"invariant_ids": ["INVARIANT-001"]},
            control_evidence=bad,
            variant_evidence=_artifact(),
        )


def test_regression_invariants_requires_requested_result_on_both_sides():
    bad = _artifact()
    bad["canonical_results"] = bad["canonical_results"][:1]
    bad["result_fingerprint"] = _fingerprint(bad["canonical_results"])
    with pytest.raises(
        ValueError,
        match="REGRESSION_INVARIANT_CONTROL_RESULT_MISSING:INVARIANT-002",
    ):
        RegressionInvariantsStageEvaluator().evaluate(
            stage_protocol={"invariant_ids": ["INVARIANT-002"]},
            control_evidence=bad,
            variant_evidence=_artifact(),
        )
