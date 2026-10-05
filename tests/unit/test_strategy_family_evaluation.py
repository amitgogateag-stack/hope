import pytest

from hope.application.experiments.evaluation_protocol import (
    REQUIRED_EVALUATION_STAGES,
    ResearchEvaluationPlanDefinition,
)
from hope.application.experiments.strategy_family_evaluation import (
    StrategyFamilyEvaluationBindingError,
    bind_strategy_family_evaluation,
)
from hope.application.experiments.strategy_family_eligibility import (
    StrategyFamilyResearchEligibilityError,
)
from hope.domain.strategy_family import ResearchMarket, ResearchRegime


def _plan(*, experiment_id: str = "exp-momentum-001") -> ResearchEvaluationPlanDefinition:
    return ResearchEvaluationPlanDefinition(
        variant_experiment_id=experiment_id,
        protocol={stage: {"enabled": True} for stage in REQUIRED_EVALUATION_STAGES},
    )


def test_eligible_family_is_bound_to_complete_evaluation_plan() -> None:
    bound = bind_strategy_family_evaluation(
        family="CROSS_SECTIONAL_MOMENTUM",
        market=ResearchMarket.USA,
        regime=ResearchRegime.BULL_TREND,
        evaluation_plan=_plan(),
    )
    assert bound.family_binding.family == "CROSS_SECTIONAL_MOMENTUM"
    assert bound.variant_experiment_id == "exp-momentum-001"
    assert len(bound.evaluation_plan_hash) == 64
    assert len(bound.research_context_hash) == 64
    assert bound.research_only is True
    assert not hasattr(bound, "paper_enabled")
    assert not hasattr(bound, "live_enabled")


def test_context_hash_is_deterministic_for_identical_research_context() -> None:
    first = bind_strategy_family_evaluation(
        family="LOW_VOLATILITY_DEFENSIVE",
        market=ResearchMarket.INDIA,
        regime=ResearchRegime.SIDEWAYS,
        evaluation_plan=_plan(experiment_id="exp-lowvol-001"),
    )
    second = bind_strategy_family_evaluation(
        family="LOW_VOLATILITY_DEFENSIVE",
        market=ResearchMarket.INDIA,
        regime=ResearchRegime.SIDEWAYS,
        evaluation_plan=_plan(experiment_id="exp-lowvol-001"),
    )
    assert first == second


def test_context_hash_changes_when_experiment_identity_changes() -> None:
    first = bind_strategy_family_evaluation(
        family="CROSS_SECTIONAL_MOMENTUM",
        market=ResearchMarket.USA,
        regime=ResearchRegime.BULL_TREND,
        evaluation_plan=_plan(experiment_id="exp-001"),
    )
    second = bind_strategy_family_evaluation(
        family="CROSS_SECTIONAL_MOMENTUM",
        market=ResearchMarket.USA,
        regime=ResearchRegime.BULL_TREND,
        evaluation_plan=_plan(experiment_id="exp-002"),
    )
    assert first.research_context_hash != second.research_context_hash


def test_context_hash_changes_when_protocol_changes() -> None:
    base = _plan()
    changed_protocol = {stage: {"enabled": True} for stage in REQUIRED_EVALUATION_STAGES}
    changed_protocol["cost_stress"] = {"enabled": True, "multipliers": [1.0, 1.5, 2.0]}
    changed = ResearchEvaluationPlanDefinition(
        variant_experiment_id=base.variant_experiment_id,
        protocol=changed_protocol,
    )
    first = bind_strategy_family_evaluation(
        family="CROSS_SECTIONAL_MOMENTUM",
        market=ResearchMarket.USA,
        regime=ResearchRegime.BULL_TREND,
        evaluation_plan=base,
    )
    second = bind_strategy_family_evaluation(
        family="CROSS_SECTIONAL_MOMENTUM",
        market=ResearchMarket.USA,
        regime=ResearchRegime.BULL_TREND,
        evaluation_plan=changed,
    )
    assert first.evaluation_plan_hash != second.evaluation_plan_hash
    assert first.research_context_hash != second.research_context_hash


def test_ineligible_family_market_regime_still_fails_closed() -> None:
    with pytest.raises(StrategyFamilyResearchEligibilityError, match="INELIGIBLE"):
        bind_strategy_family_evaluation(
            family="EARNINGS_DRIFT",
            market=ResearchMarket.INDIA,
            regime=ResearchRegime.BULL_TREND,
            evaluation_plan=_plan(),
        )


def test_noncanonical_plan_object_is_rejected_before_family_binding() -> None:
    with pytest.raises(StrategyFamilyEvaluationBindingError, match="PLAN_NOT_CANONICAL"):
        bind_strategy_family_evaluation(
            family="CROSS_SECTIONAL_MOMENTUM",
            market=ResearchMarket.USA,
            regime=ResearchRegime.BULL_TREND,
            evaluation_plan={"variant_experiment_id": "forged"},  # type: ignore[arg-type]
        )
