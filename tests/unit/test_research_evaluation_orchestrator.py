from types import SimpleNamespace
from uuid import uuid4

import pytest

from hope.application.experiments.evaluation_orchestrator import (
    ResearchEvaluationOrchestrator,
)
from hope.application.experiments.evaluation_protocol import (
    REQUIRED_EVALUATION_STAGES,
    research_evaluation_plan_hash,
)


def _protocol():
    return {stage: {"enabled": True} for stage in REQUIRED_EVALUATION_STAGES}


class _Plans:
    def __init__(self, plan):
        self.plan = plan

    def get(self, variant_experiment_id):
        return self.plan


class _Evidence:
    def __init__(self, values):
        self.values = values

    def get(self, run_id):
        return self.values.get(run_id)


class _Results:
    def __init__(self):
        self.persisted = []

    def persist(self, definition):
        self.persisted.append(definition)
        return True


class _Evaluator:
    stage = "regression_invariants"

    def evaluate(self, *, stage_protocol, control_evidence, variant_evidence):
        return {
            "stage_protocol": dict(stage_protocol),
            "control_fingerprint_input": control_evidence["value"],
            "variant_fingerprint_input": variant_evidence["value"],
        }


def _orchestrator(
    *,
    plan=None,
    evidence=None,
    evaluators=None,
    stage_evidence_repositories=None,
):
    protocol = _protocol()
    if plan is None:
        plan = SimpleNamespace(
            protocol_hash=research_evaluation_plan_hash(protocol),
            canonical_protocol=protocol,
        )
    results = _Results()
    generic_evidence = _Evidence(evidence or {})
    stage_sources = stage_evidence_repositories
    if stage_sources is None:
        stage_sources = {"regression_invariants": generic_evidence}
    orchestrator = ResearchEvaluationOrchestrator(
        plan_repository=_Plans(plan),
        evidence_repository=generic_evidence,
        result_repository=results,
        evaluators=tuple(evaluators or (_Evaluator(),)),
        stage_evidence_repositories=stage_sources,
    )
    return orchestrator, results


def test_stage_orchestrator_persists_deterministic_result_from_exact_evidence():
    control_run_id, variant_run_id = uuid4(), uuid4()
    orchestrator, results = _orchestrator(
        evidence={
            control_run_id: SimpleNamespace(canonical_result={"value": "control"}),
            variant_run_id: SimpleNamespace(canonical_result={"value": "variant"}),
        }
    )

    definition = orchestrator.evaluate_stage(
        variant_experiment_id="EXP-VARIANT",
        control_run_id=control_run_id,
        variant_run_id=variant_run_id,
        stage="regression_invariants",
    )

    assert results.persisted == [definition]
    assert definition.canonical_result["control_fingerprint_input"] == "control"
    assert definition.canonical_result["variant_fingerprint_input"] == "variant"


def test_stage_orchestrator_fails_closed_without_registered_evaluator():
    control_run_id, variant_run_id = uuid4(), uuid4()
    orchestrator, _ = _orchestrator(
        evidence={
            control_run_id: SimpleNamespace(canonical_result={"value": "control"}),
            variant_run_id: SimpleNamespace(canonical_result={"value": "variant"}),
        },
        evaluators=(),
    )

    with pytest.raises(ValueError, match="RESEARCH_STAGE_EVALUATOR_MISSING"):
        orchestrator.evaluate_stage(
            variant_experiment_id="EXP-VARIANT",
            control_run_id=control_run_id,
            variant_run_id=variant_run_id,
            stage="historical_evaluation",
        )


def test_stage_orchestrator_rejects_tampered_protocol_hash():
    protocol = _protocol()
    plan = SimpleNamespace(
        protocol_hash="a" * 64,
        canonical_protocol=protocol,
    )
    orchestrator, _ = _orchestrator(plan=plan)

    with pytest.raises(ValueError, match="RESEARCH_EVALUATION_PLAN_HASH_MISMATCH"):
        orchestrator.evaluate_stage(
            variant_experiment_id="EXP-VARIANT",
            control_run_id=uuid4(),
            variant_run_id=uuid4(),
            stage="regression_invariants",
        )


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("control", "RESEARCH_EVALUATION_CONTROL_EVIDENCE_MISSING"),
        ("variant", "RESEARCH_EVALUATION_VARIANT_EVIDENCE_MISSING"),
    ],
)
def test_stage_orchestrator_requires_exact_pair_evidence(missing, message):
    control_run_id, variant_run_id = uuid4(), uuid4()
    evidence = {
        control_run_id: SimpleNamespace(canonical_result={"value": "control"}),
        variant_run_id: SimpleNamespace(canonical_result={"value": "variant"}),
    }
    evidence.pop(control_run_id if missing == "control" else variant_run_id)
    orchestrator, results = _orchestrator(evidence=evidence)

    with pytest.raises(ValueError, match=message):
        orchestrator.evaluate_stage(
            variant_experiment_id="EXP-VARIANT",
            control_run_id=control_run_id,
            variant_run_id=variant_run_id,
            stage="regression_invariants",
        )
    assert results.persisted == []


def test_stage_orchestrator_rejects_placeholder_or_empty_result():
    class EmptyEvaluator:
        stage = "regression_invariants"

        def evaluate(self, *, stage_protocol, control_evidence, variant_evidence):
            return {}

    control_run_id, variant_run_id = uuid4(), uuid4()
    orchestrator, results = _orchestrator(
        evidence={
            control_run_id: SimpleNamespace(canonical_result={"value": "control"}),
            variant_run_id: SimpleNamespace(canonical_result={"value": "variant"}),
        },
        evaluators=(EmptyEvaluator(),),
    )

    with pytest.raises(ValueError, match="RESEARCH_STAGE_EVALUATOR_RESULT_INVALID"):
        orchestrator.evaluate_stage(
            variant_experiment_id="EXP-VARIANT",
            control_run_id=control_run_id,
            variant_run_id=variant_run_id,
            stage="regression_invariants",
        )
    assert results.persisted == []



def test_stage_orchestrator_can_use_stage_specific_evidence_repository():
    class InvariantRecord:
        def __init__(self, label):
            self.label = label
            self.created_at = None

        def model_dump(self, exclude=None):
            return {"label": self.label}

    class InvariantEvaluator:
        stage = "regression_invariants"

        def evaluate(self, *, stage_protocol, control_evidence, variant_evidence):
            return {
                "control": control_evidence["label"],
                "variant": variant_evidence["label"],
            }

    control_run_id, variant_run_id = uuid4(), uuid4()
    protocol = _protocol()
    plan = SimpleNamespace(
        protocol_hash=research_evaluation_plan_hash(protocol),
        canonical_protocol=protocol,
    )
    results = _Results()
    invariant_repo = _Evidence(
        {
            control_run_id: InvariantRecord("control-invariants"),
            variant_run_id: InvariantRecord("variant-invariants"),
        }
    )
    orchestrator = ResearchEvaluationOrchestrator(
        plan_repository=_Plans(plan),
        evidence_repository=_Evidence({}),
        result_repository=results,
        evaluators=(InvariantEvaluator(),),
        stage_evidence_repositories={"regression_invariants": invariant_repo},
    )

    definition = orchestrator.evaluate_stage(
        variant_experiment_id="EXP-VARIANT",
        control_run_id=control_run_id,
        variant_run_id=variant_run_id,
        stage="regression_invariants",
    )

    assert definition.canonical_result == {
        "control": "control-invariants",
        "variant": "variant-invariants",
    }


def test_stage_orchestrator_fails_closed_when_stage_specific_evidence_missing():
    control_run_id, variant_run_id = uuid4(), uuid4()
    protocol = _protocol()
    plan = SimpleNamespace(
        protocol_hash=research_evaluation_plan_hash(protocol),
        canonical_protocol=protocol,
    )
    orchestrator = ResearchEvaluationOrchestrator(
        plan_repository=_Plans(plan),
        evidence_repository=_Evidence({}),
        result_repository=_Results(),
        evaluators=(_Evaluator(),),
        stage_evidence_repositories={
            "regression_invariants": _Evidence({})
        },
    )
    with pytest.raises(
        ValueError,
        match="RESEARCH_EVALUATION_CONTROL_EVIDENCE_MISSING",
    ):
        orchestrator.evaluate_stage(
            variant_experiment_id="EXP-VARIANT",
            control_run_id=control_run_id,
            variant_run_id=variant_run_id,
            stage="regression_invariants",
        )


def test_stage_orchestrator_rejects_generic_fallback_for_derived_stage():
    class WalkForwardEvaluator:
        stage = "walk_forward"

        def evaluate(self, *, stage_protocol, control_evidence, variant_evidence):
            return {"control": control_evidence, "variant": variant_evidence}

    control_run_id, variant_run_id = uuid4(), uuid4()
    orchestrator, results = _orchestrator(
        evidence={
            control_run_id: SimpleNamespace(canonical_result={"value": "control"}),
            variant_run_id: SimpleNamespace(canonical_result={"value": "variant"}),
        },
        evaluators=(WalkForwardEvaluator(),),
        stage_evidence_repositories={},
    )

    with pytest.raises(
        ValueError,
        match="RESEARCH_STAGE_EVIDENCE_SOURCE_MISSING",
    ):
        orchestrator.evaluate_stage(
            variant_experiment_id="EXP-VARIANT",
            control_run_id=control_run_id,
            variant_run_id=variant_run_id,
            stage="walk_forward",
        )
    assert results.persisted == []


def test_stage_orchestrator_allows_generic_source_only_for_historical_evaluation():
    class HistoricalEvaluator:
        stage = "historical_evaluation"

        def evaluate(self, *, stage_protocol, control_evidence, variant_evidence):
            return {
                "control": control_evidence["value"],
                "variant": variant_evidence["value"],
            }

    control_run_id, variant_run_id = uuid4(), uuid4()
    orchestrator, results = _orchestrator(
        evidence={
            control_run_id: SimpleNamespace(canonical_result={"value": "control"}),
            variant_run_id: SimpleNamespace(canonical_result={"value": "variant"}),
        },
        evaluators=(HistoricalEvaluator(),),
        stage_evidence_repositories={},
    )

    definition = orchestrator.evaluate_stage(
        variant_experiment_id="EXP-VARIANT",
        control_run_id=control_run_id,
        variant_run_id=variant_run_id,
        stage="historical_evaluation",
    )

    assert definition.canonical_result == {
        "control": "control",
        "variant": "variant",
    }
    assert results.persisted == [definition]
