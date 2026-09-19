import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.slippage_stress import (
    SLIPPAGE_STRESS_EVIDENCE_SCHEMA,
    SlippageStressStageEvaluator,
)


def _scenario(scenario_id, assumptions, pnl, sharpe="1.0"):
    return {
        "scenario_id": scenario_id,
        "slippage_assumptions": assumptions,
        "metrics": {"total_pnl": str(pnl), "sharpe": str(sharpe)},
    }


def _artifact(scenarios):
    return {
        "schema": SLIPPAGE_STRESS_EVIDENCE_SCHEMA,
        "scenarios": scenarios,
        "result_fingerprint": configuration_hash(scenarios),
    }


def test_slippage_stress_compares_predeclared_scenarios_without_verdict():
    assumptions = [
        {"entry_bps": "2", "exit_bps": "2"},
        {"entry_bps": "6", "exit_bps": "6"},
    ]
    control = _artifact([
        _scenario("base", assumptions[0], 100, "1.0"),
        _scenario("triple", assumptions[1], 60, "0.6"),
    ])
    variant = _artifact([
        _scenario("base", assumptions[0], 120, "1.1"),
        _scenario("triple", assumptions[1], 75, "0.7"),
    ])

    result = SlippageStressStageEvaluator().evaluate(
        stage_protocol={
            "scenario_ids": ["base", "triple"],
            "metrics": ["total_pnl", "sharpe"],
        },
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.slippage-stress-evaluation.v1"
    assert result["scenarios"]["triple"]["metrics"]["total_pnl"]["delta"] == "15"
    assert result["scenarios"]["base"]["slippage_assumptions"] == assumptions[0]
    assert "winner" not in result
    assert "pass" not in result


def test_slippage_stress_requires_predeclared_scenarios_and_metrics():
    evidence = _artifact([
        _scenario("base", {"entry_bps": "2", "exit_bps": "2"}, 100)
    ])
    evaluator = SlippageStressStageEvaluator()

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_SCENARIOS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_METRICS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"scenario_ids": ["base"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_slippage_stress_rejects_tampered_evidence():
    evidence = _artifact([
        _scenario("base", {"entry_bps": "2", "exit_bps": "2"}, 100)
    ])
    evidence["scenarios"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_EVIDENCE_FINGERPRINT_MISMATCH"):
        SlippageStressStageEvaluator().evaluate(
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=_artifact([
                _scenario("base", {"entry_bps": "2", "exit_bps": "2"}, 100)
            ]),
        )


def test_slippage_stress_rejects_scenario_identity_drift():
    control = _artifact([
        _scenario("base", {"entry_bps": "2", "exit_bps": "2"}, 100)
    ])
    variant = _artifact([
        _scenario("other", {"entry_bps": "2", "exit_bps": "2"}, 100)
    ])

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_VARIANT_SCENARIOS_MISMATCH"):
        SlippageStressStageEvaluator().evaluate(
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            control_evidence=control,
            variant_evidence=variant,
        )


def test_slippage_stress_rejects_assumption_drift():
    control = _artifact([
        _scenario("base", {"entry_bps": "2", "exit_bps": "2"}, 100)
    ])
    variant = _artifact([
        _scenario("base", {"entry_bps": "3", "exit_bps": "2"}, 100)
    ])

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_ASSUMPTIONS_MISMATCH:base"):
        SlippageStressStageEvaluator().evaluate(
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            control_evidence=control,
            variant_evidence=variant,
        )
