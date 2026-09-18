from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from hope.application.experiments.config_hash import canonical_json


REQUIRED_EVALUATION_STAGES = (
    "regression_invariants",
    "historical_evaluation",
    "walk_forward",
    "regime_analysis",
    "parameter_sensitivity",
    "cost_stress",
    "slippage_stress",
    "universe_perturbation",
    "contribution_analysis",
)


class ResearchEvaluationPlanDefinition(BaseModel):
    """Predeclared evaluation protocol for one control/variant research pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    variant_experiment_id: str
    protocol: dict[str, Any]

    @field_validator("variant_experiment_id")
    @classmethod
    def require_canonical_variant_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RESEARCH_EVALUATION_VARIANT_ID_REQUIRED")
        if value != value.strip():
            raise ValueError("RESEARCH_EVALUATION_VARIANT_ID_NOT_CANONICAL")
        return value

    @field_validator("protocol")
    @classmethod
    def require_complete_protocol(cls, value: dict[str, Any]) -> dict[str, Any]:
        missing = [stage for stage in REQUIRED_EVALUATION_STAGES if stage not in value]
        if missing:
            raise ValueError(
                "RESEARCH_EVALUATION_REQUIRED_STAGES_MISSING:" + ",".join(missing)
            )
        for stage in REQUIRED_EVALUATION_STAGES:
            if not isinstance(value[stage], dict) or not value[stage]:
                raise ValueError(
                    f"RESEARCH_EVALUATION_STAGE_CONFIGURATION_REQUIRED:{stage}"
                )
        return value


def research_evaluation_plan_hash(protocol: Any) -> str:
    return hashlib.sha256(canonical_json(protocol).encode("utf-8")).hexdigest()
