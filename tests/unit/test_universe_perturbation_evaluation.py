import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.universe_perturbation import (
    UNIVERSE_PERTURBATION_EVIDENCE_SCHEMA,
    UniversePerturbationStageEvaluator,
)


def _perturbation(perturbation_id, universe_definition, pnl, sharpe="1.0"):
    return {
        "perturbation_id": perturbation_id,
        "universe_definition": universe_definition,
        "metrics": {"total_pnl": str(pnl), "sharpe": str(sharpe)},
    }


def _artifact(perturbations):
    return {
        "schema": UNIVERSE_PERTURBATION_EVIDENCE_SCHEMA,
        "perturbations": perturbations,
        "result_fingerprint": configuration_hash(perturbations),
    }


def test_universe_perturbation_compares_predeclared_variants_without_verdict():
    definitions = [
        {"method": "baseline", "symbols_hash": "abc123"},
        {"method": "drop_random_fraction", "fraction": "0.10", "seed": 17},
    ]
    control = _artifact([
        _perturbation("baseline", definitions[0], 100, "1.0"),
        _perturbation("drop10", definitions[1], 80, "0.8"),
    ])
    variant = _artifact([
        _perturbation("baseline", definitions[0], 120, "1.1"),
        _perturbation("drop10", definitions[1], 95, "0.9"),
    ])

    result = UniversePerturbationStageEvaluator().evaluate(
        stage_protocol={
            "perturbation_ids": ["baseline", "drop10"],
            "metrics": ["total_pnl", "sharpe"],
        },
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.universe-perturbation-evaluation.v1"
    assert result["perturbations"]["drop10"]["metrics"]["total_pnl"]["delta"] == "15"
    assert result["perturbations"]["baseline"]["universe_definition"] == definitions[0]
    assert "winner" not in result
    assert "best" not in result
    assert "pass" not in result


def test_universe_perturbation_requires_predeclared_ids_and_metrics():
    evidence = _artifact([
        _perturbation("baseline", {"method": "baseline"}, 100)
    ])
    evaluator = UniversePerturbationStageEvaluator()

    with pytest.raises(ValueError, match="UNIVERSE_PERTURBATION_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_METRICS_PREDECLARATION_REQUIRED",
    ):
        evaluator.evaluate(
            stage_protocol={"perturbation_ids": ["baseline"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_universe_perturbation_rejects_tampered_evidence():
    evidence = _artifact([
        _perturbation("baseline", {"method": "baseline"}, 100)
    ])
    evidence["perturbations"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_EVIDENCE_FINGERPRINT_MISMATCH",
    ):
        UniversePerturbationStageEvaluator().evaluate(
            stage_protocol={
                "perturbation_ids": ["baseline"],
                "metrics": ["total_pnl"],
            },
            control_evidence=evidence,
            variant_evidence=_artifact([
                _perturbation("baseline", {"method": "baseline"}, 100)
            ]),
        )


def test_universe_perturbation_rejects_identity_drift():
    control = _artifact([
        _perturbation("baseline", {"method": "baseline"}, 100)
    ])
    variant = _artifact([
        _perturbation("other", {"method": "baseline"}, 100)
    ])

    with pytest.raises(ValueError, match="UNIVERSE_PERTURBATION_VARIANT_IDS_MISMATCH"):
        UniversePerturbationStageEvaluator().evaluate(
            stage_protocol={
                "perturbation_ids": ["baseline"],
                "metrics": ["total_pnl"],
            },
            control_evidence=control,
            variant_evidence=variant,
        )


def test_universe_perturbation_rejects_definition_drift():
    control = _artifact([
        _perturbation(
            "drop10",
            {"method": "drop_random_fraction", "fraction": "0.10", "seed": 17},
            100,
        )
    ])
    variant = _artifact([
        _perturbation(
            "drop10",
            {"method": "drop_random_fraction", "fraction": "0.10", "seed": 18},
            100,
        )
    ])

    with pytest.raises(
        ValueError,
        match="UNIVERSE_PERTURBATION_DEFINITION_MISMATCH:drop10",
    ):
        UniversePerturbationStageEvaluator().evaluate(
            stage_protocol={
                "perturbation_ids": ["drop10"],
                "metrics": ["total_pnl"],
            },
            control_evidence=control,
            variant_evidence=variant,
        )
