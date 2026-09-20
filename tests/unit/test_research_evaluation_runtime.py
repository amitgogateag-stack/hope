from hope.application.experiments.evaluation_protocol import (
    REQUIRED_EVALUATION_STAGES,
)
from hope.infrastructure.repositories.research_evaluation_plans import (
    SqlAlchemyResearchEvaluationPlanRepository,
)
from hope.infrastructure.repositories.research_evaluation_results import (
    SqlAlchemyResearchEvaluationResultRepository,
)
from hope.infrastructure.repositories.research_invariant_runs import (
    SqlAlchemyResearchInvariantRunRepository,
)
from hope.infrastructure.repositories.research_run_evidence import (
    SqlAlchemyResearchRunEvidenceRepository,
)
from hope.infrastructure.repositories.research_stage_evidence import (
    GENERIC_STAGE_EVIDENCE_STAGES,
    SqlAlchemyResearchStageEvidenceRepository,
)
from hope.infrastructure.research_evaluation_runtime import (
    build_research_evaluation_orchestrator,
)


def test_runtime_composition_wires_every_required_evaluator_and_evidence_source():
    connection = object()

    orchestrator = build_research_evaluation_orchestrator(connection)

    assert tuple(orchestrator._evaluators) == REQUIRED_EVALUATION_STAGES
    assert isinstance(
        orchestrator._plans,
        SqlAlchemyResearchEvaluationPlanRepository,
    )
    assert isinstance(
        orchestrator._evidence,
        SqlAlchemyResearchRunEvidenceRepository,
    )
    assert isinstance(
        orchestrator._results,
        SqlAlchemyResearchEvaluationResultRepository,
    )

    expected_stage_sources = set(REQUIRED_EVALUATION_STAGES) - {
        "historical_evaluation"
    }
    assert set(orchestrator._stage_evidence) == expected_stage_sources
    assert isinstance(
        orchestrator._stage_evidence["regression_invariants"],
        SqlAlchemyResearchInvariantRunRepository,
    )

    for stage in GENERIC_STAGE_EVIDENCE_STAGES:
        repository = orchestrator._stage_evidence[stage]
        assert isinstance(repository, SqlAlchemyResearchStageEvidenceRepository)
        assert repository.stage == stage


def test_runtime_composition_never_registers_generic_historical_repository_as_stage_source():
    orchestrator = build_research_evaluation_orchestrator(object())

    assert "historical_evaluation" not in orchestrator._stage_evidence
    assert isinstance(
        orchestrator._evidence,
        SqlAlchemyResearchRunEvidenceRepository,
    )
