from __future__ import annotations

import hashlib
from dataclasses import dataclass

from hope.application.experiments.config_hash import canonical_json
from hope.application.experiments.evaluation_protocol import ResearchEvaluationPlanDefinition
from hope.application.experiments.strategy_family_eligibility import (
    StrategyFamilyResearchBinding,
    bind_strategy_family_research,
)
from hope.domain.strategy_family import ResearchMarket, ResearchRegime


class StrategyFamilyEvaluationBindingError(ValueError):
    """Raised when an evaluation plan cannot be durably bound to research eligibility."""


@dataclass(frozen=True, slots=True)
class BoundStrategyFamilyEvaluation:
    family_binding: StrategyFamilyResearchBinding
    variant_experiment_id: str
    evaluation_plan_hash: str
    research_context_hash: str
    research_only: bool = True


def bind_strategy_family_evaluation(
    *,
    family: str,
    market: ResearchMarket,
    regime: ResearchRegime,
    evaluation_plan: ResearchEvaluationPlanDefinition,
) -> BoundStrategyFamilyEvaluation:
    """Authenticate family eligibility and freeze it to one complete evaluation plan.

    The resulting context hash commits the experiment identity, family, market,
    regime and canonical evaluation protocol. It is research evidence only and
    cannot authorize PAPER or LIVE execution.
    """
    if not isinstance(evaluation_plan, ResearchEvaluationPlanDefinition):
        raise StrategyFamilyEvaluationBindingError(
            "RESEARCH_EVALUATION_PLAN_NOT_CANONICAL"
        )

    family_binding = bind_strategy_family_research(
        family=family,
        market=market,
        regime=regime,
    )
    if family_binding.research_only is not True:
        raise StrategyFamilyEvaluationBindingError(
            "RESEARCH_FAMILY_BINDING_NOT_RESEARCH_ONLY"
        )

    protocol_payload = evaluation_plan.protocol
    evaluation_plan_hash = hashlib.sha256(
        canonical_json(protocol_payload).encode("utf-8")
    ).hexdigest()
    context_payload = {
        "family": family_binding.family,
        "market": family_binding.market.value,
        "regime": family_binding.regime.value,
        "research_only": True,
        "variant_experiment_id": evaluation_plan.variant_experiment_id,
        "evaluation_plan_hash": evaluation_plan_hash,
    }
    research_context_hash = hashlib.sha256(
        canonical_json(context_payload).encode("utf-8")
    ).hexdigest()

    return BoundStrategyFamilyEvaluation(
        family_binding=family_binding,
        variant_experiment_id=evaluation_plan.variant_experiment_id,
        evaluation_plan_hash=evaluation_plan_hash,
        research_context_hash=research_context_hash,
    )
