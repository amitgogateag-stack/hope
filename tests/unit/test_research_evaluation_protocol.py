import pytest

from hope.application.experiments.evaluation_protocol import (
    REQUIRED_EVALUATION_STAGES,
    ResearchEvaluationPlanDefinition,
    research_evaluation_plan_hash,
)


def complete_protocol() -> dict[str, dict[str, object]]:
    return {stage: {"enabled": True} for stage in REQUIRED_EVALUATION_STAGES}


def test_evaluation_plan_requires_every_research_stage() -> None:
    protocol = complete_protocol()
    protocol.pop("cost_stress")
    with pytest.raises(ValueError, match="RESEARCH_EVALUATION_REQUIRED_STAGES_MISSING:cost_stress"):
        ResearchEvaluationPlanDefinition(
            variant_experiment_id="EXP-VARIANT",
            protocol=protocol,
        )


def test_evaluation_plan_rejects_empty_stage_configuration() -> None:
    protocol = complete_protocol()
    protocol["walk_forward"] = {}
    with pytest.raises(
        ValueError,
        match="RESEARCH_EVALUATION_STAGE_CONFIGURATION_REQUIRED:walk_forward",
    ):
        ResearchEvaluationPlanDefinition(
            variant_experiment_id="EXP-VARIANT",
            protocol=protocol,
        )


def test_evaluation_plan_hash_is_canonical_and_content_sensitive() -> None:
    protocol = complete_protocol()
    reordered = dict(reversed(list(protocol.items())))
    changed = complete_protocol()
    changed["cost_stress"] = {"enabled": True, "multipliers": [1, 2]}

    assert research_evaluation_plan_hash(protocol) == research_evaluation_plan_hash(reordered)
    assert research_evaluation_plan_hash(protocol) != research_evaluation_plan_hash(changed)
