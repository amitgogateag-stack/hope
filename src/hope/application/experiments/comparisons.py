from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from hope.application.experiments.config_hash import canonical_json
from hope.application.experiments.evaluation_protocol import REQUIRED_EVALUATION_STAGES


def build_research_comparison(
    *,
    variant_experiment_id: str,
    control_run_id: UUID,
    variant_run_id: UUID,
    control_result_fingerprint: str,
    variant_result_fingerprint: str,
    protocol_hash: str,
    stage_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    missing = [stage for stage in REQUIRED_EVALUATION_STAGES if stage not in stage_results]
    extra = sorted(set(stage_results) - set(REQUIRED_EVALUATION_STAGES))
    if missing:
        raise ValueError("RESEARCH_COMPARISON_STAGE_RESULTS_MISSING:" + ",".join(missing))
    if extra:
        raise ValueError("RESEARCH_COMPARISON_STAGE_RESULTS_UNEXPECTED:" + ",".join(extra))

    return {
        "schema": "hope.research-comparison.v2",
        "variant_experiment_id": variant_experiment_id,
        "control": {
            "run_id": str(control_run_id),
            "result_fingerprint": control_result_fingerprint,
        },
        "variant": {
            "run_id": str(variant_run_id),
            "result_fingerprint": variant_result_fingerprint,
        },
        "evaluation_protocol_hash": protocol_hash,
        "evaluation_results": {
            stage: stage_results[stage] for stage in REQUIRED_EVALUATION_STAGES
        },
    }


def research_comparison_fingerprint(canonical_comparison: Any) -> str:
    return hashlib.sha256(
        canonical_json(canonical_comparison).encode("utf-8")
    ).hexdigest()
