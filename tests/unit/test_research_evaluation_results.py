from uuid import uuid4

import pytest

from hope.application.experiments.evaluation_results import (
    ResearchEvaluationResultDefinition,
    research_evaluation_result_fingerprint,
)


def test_evaluation_result_fingerprint_is_canonical() -> None:
    a = {"summary": {"trades": 5, "net": "10"}}
    b = {"summary": {"net": "10", "trades": 5}}
    assert research_evaluation_result_fingerprint(a) == research_evaluation_result_fingerprint(b)


def test_evaluation_result_rejects_unknown_stage() -> None:
    with pytest.raises(ValueError, match="RESEARCH_EVALUATION_RESULT_STAGE_INVALID"):
        ResearchEvaluationResultDefinition(
            variant_experiment_id="EXP-VARIANT",
            control_run_id=uuid4(),
            variant_run_id=uuid4(),
            stage="made_up",
            protocol_hash="a" * 64,
            canonical_result={"ok": True},
            result_fingerprint=research_evaluation_result_fingerprint({"ok": True}),
        )
