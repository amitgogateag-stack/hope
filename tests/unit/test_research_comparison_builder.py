from uuid import UUID

import pytest

from hope.application.experiments.comparisons import (
    build_research_comparison,
    research_comparison_fingerprint,
)
from hope.application.experiments.evaluation_protocol import REQUIRED_EVALUATION_STAGES


CONTROL_RUN = UUID("00000000-0000-0000-0000-000000000001")
VARIANT_RUN = UUID("00000000-0000-0000-0000-000000000002")


def stage_results() -> dict[str, dict]:
    return {
        stage: {
            "result_fingerprint": f"{index:064x}",
            "result": {"stage": stage, "status": "RECORDED"},
        }
        for index, stage in enumerate(REQUIRED_EVALUATION_STAGES, start=1)
    }


def test_comparison_builder_is_deterministic_and_complete() -> None:
    results = stage_results()
    comparison = build_research_comparison(
        variant_experiment_id="EXP-VARIANT",
        control_run_id=CONTROL_RUN,
        variant_run_id=VARIANT_RUN,
        control_result_fingerprint="a" * 64,
        variant_result_fingerprint="b" * 64,
        protocol_hash="c" * 64,
        stage_results=results,
    )
    reversed_results = dict(reversed(list(results.items())))
    comparison_reordered = build_research_comparison(
        variant_experiment_id="EXP-VARIANT",
        control_run_id=CONTROL_RUN,
        variant_run_id=VARIANT_RUN,
        control_result_fingerprint="a" * 64,
        variant_result_fingerprint="b" * 64,
        protocol_hash="c" * 64,
        stage_results=reversed_results,
    )

    assert comparison == comparison_reordered
    assert research_comparison_fingerprint(comparison) == research_comparison_fingerprint(
        comparison_reordered
    )
    assert list(comparison["evaluation_results"]) == list(REQUIRED_EVALUATION_STAGES)


def test_comparison_builder_rejects_missing_stage() -> None:
    results = stage_results()
    results.pop("cost_stress")
    with pytest.raises(ValueError, match="RESEARCH_COMPARISON_STAGE_RESULTS_MISSING:cost_stress"):
        build_research_comparison(
            variant_experiment_id="EXP-VARIANT",
            control_run_id=CONTROL_RUN,
            variant_run_id=VARIANT_RUN,
            control_result_fingerprint="a" * 64,
            variant_result_fingerprint="b" * 64,
            protocol_hash="c" * 64,
            stage_results=results,
        )
