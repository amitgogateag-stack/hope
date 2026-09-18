from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from uuid import UUID

from hope.application.experiments.config_hash import canonical_json
from hope.application.experiments.evaluation_protocol import REQUIRED_EVALUATION_STAGES


class ResearchEvaluationResultDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    variant_experiment_id: str
    control_run_id: UUID
    variant_run_id: UUID
    stage: str
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_result: dict[str, Any]
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("stage")
    @classmethod
    def require_known_stage(cls, value: str) -> str:
        if value not in REQUIRED_EVALUATION_STAGES:
            raise ValueError("RESEARCH_EVALUATION_RESULT_STAGE_INVALID")
        return value


def research_evaluation_result_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
