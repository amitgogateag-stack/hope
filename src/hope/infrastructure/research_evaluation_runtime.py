from __future__ import annotations

from sqlalchemy import Connection

from hope.application.experiments.contribution_analysis import (
    ContributionAnalysisStageEvaluator,
)
from hope.application.experiments.cost_stress import CostStressStageEvaluator
from hope.application.experiments.evaluation_orchestrator import (
    ResearchEvaluationOrchestrator,
)
from hope.application.experiments.evaluation_protocol import REQUIRED_EVALUATION_STAGES
from hope.application.experiments.historical_evaluation import (
    HistoricalEvaluationStageEvaluator,
)
from hope.application.experiments.parameter_sensitivity import (
    ParameterSensitivityStageEvaluator,
)
from hope.application.experiments.regime_analysis import RegimeAnalysisStageEvaluator
from hope.application.experiments.regression_invariants import (
    RegressionInvariantsStageEvaluator,
)
from hope.application.experiments.slippage_stress import SlippageStressStageEvaluator
from hope.application.experiments.universe_perturbation import (
    UniversePerturbationStageEvaluator,
)
from hope.application.experiments.walk_forward import WalkForwardStageEvaluator
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
    build_research_stage_evidence_repositories,
)


def build_research_evaluation_orchestrator(
    connection: Connection,
) -> ResearchEvaluationOrchestrator:
    """Build the canonical durable research-evaluation runtime boundary."""

    invariant_repository = SqlAlchemyResearchInvariantRunRepository(connection)
    stage_repositories = build_research_stage_evidence_repositories(
        connection,
        invariant_repository=invariant_repository,
    )
    expected_stage_sources = set(REQUIRED_EVALUATION_STAGES) - {
        "historical_evaluation"
    }
    if set(stage_repositories) != expected_stage_sources:
        raise RuntimeError("RESEARCH_EVALUATION_STAGE_SOURCE_COMPOSITION_INCOMPLETE")

    evaluators = (
        RegressionInvariantsStageEvaluator(),
        HistoricalEvaluationStageEvaluator(),
        WalkForwardStageEvaluator(),
        RegimeAnalysisStageEvaluator(),
        ParameterSensitivityStageEvaluator(),
        CostStressStageEvaluator(),
        SlippageStressStageEvaluator(),
        UniversePerturbationStageEvaluator(),
        ContributionAnalysisStageEvaluator(),
    )
    if tuple(evaluator.stage for evaluator in evaluators) != REQUIRED_EVALUATION_STAGES:
        raise RuntimeError("RESEARCH_EVALUATION_EVALUATOR_COMPOSITION_INCOMPLETE")

    return ResearchEvaluationOrchestrator(
        plan_repository=SqlAlchemyResearchEvaluationPlanRepository(connection),
        evidence_repository=SqlAlchemyResearchRunEvidenceRepository(connection),
        result_repository=SqlAlchemyResearchEvaluationResultRepository(connection),
        evaluators=evaluators,
        stage_evidence_repositories=stage_repositories,
    )
