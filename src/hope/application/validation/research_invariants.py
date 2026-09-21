from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hope.application.experiments.config_hash import canonical_json
from hope.application.validation.runner import InvariantRun, InvariantRunner
from hope.domain.validation.contexts import InvariantContext


class ResearchInvariantRunArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    research_run_id: UUID
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_results: list[dict[str, str]]

    @field_validator("canonical_results")
    @classmethod
    def require_canonical_result_shape(
        cls, value: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        required = {"invariant_id", "status", "message"}
        for result in value:
            if set(result) != required:
                raise ValueError("RESEARCH_INVARIANT_RESULT_SHAPE_INVALID")
            if not result["invariant_id"].strip() or result["invariant_id"] != result["invariant_id"].strip():
                raise ValueError("RESEARCH_INVARIANT_ID_NOT_CANONICAL")
            if result["status"] not in {"PASS", "FAIL", "SKIP"}:
                raise ValueError("RESEARCH_INVARIANT_STATUS_INVALID")
        return value


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def invariant_context_fingerprint(context: InvariantContext) -> str:
    if not isinstance(context, InvariantContext):
        raise TypeError("RESEARCH_INVARIANT_CONTEXT_REQUIRED")
    return _fingerprint(asdict(context))


def invariant_run_results(run: InvariantRun) -> list[dict[str, str]]:
    if not isinstance(run, InvariantRun):
        raise TypeError("RESEARCH_INVARIANT_RUN_REQUIRED")
    return [
        {
            "invariant_id": result.invariant_id,
            "status": result.status.value,
            "message": result.message,
        }
        for result in run.results
    ]


def build_research_invariant_artifact(
    research_run_id: UUID,
    context: InvariantContext,
    *,
    runner: InvariantRunner | None = None,
) -> ResearchInvariantRunArtifact:
    if not isinstance(research_run_id, UUID):
        raise TypeError("RESEARCH_INVARIANT_RUN_ID_REQUIRED")
    actual_runner = runner or InvariantRunner()
    run = actual_runner.run(context)
    canonical_results = invariant_run_results(run)
    return ResearchInvariantRunArtifact(
        research_run_id=research_run_id,
        context_fingerprint=invariant_context_fingerprint(context),
        result_fingerprint=_fingerprint(canonical_results),
        canonical_results=canonical_results,
    )
