from uuid import uuid4

import pytest

from hope.application.experiments.evaluation_results import (
    ResearchEvaluationResultDefinition,
    research_evaluation_result_fingerprint,
)
from hope.infrastructure.repositories.research_evaluation_results import (
    SqlAlchemyResearchEvaluationResultRepository,
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


def test_evaluation_result_repository_rejects_tampered_stored_result() -> None:
    class Rows:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "variant_experiment_id": "EXP-VARIANT",
                    "control_run_id": control_run_id,
                    "variant_run_id": variant_run_id,
                    "stage": "walk_forward",
                    "protocol_hash": "a" * 64,
                    "result_fingerprint": "0" * 64,
                    "canonical_result": {"status": "tampered"},
                    "created_at": None,
                }
            ]

    class Connection:
        def execute(self, statement):
            return Rows()

    control_run_id, variant_run_id = uuid4(), uuid4()
    repository = SqlAlchemyResearchEvaluationResultRepository(Connection())

    with pytest.raises(
        ValueError,
        match="RESEARCH_EVALUATION_STORED_RESULT_FINGERPRINT_MISMATCH",
    ):
        repository.list_by_run_pair(
            "EXP-VARIANT",
            control_run_id,
            variant_run_id,
        )
