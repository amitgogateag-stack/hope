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


def _protocol(ids, definitions, metrics=("total_pnl", "sharpe")):
    return {
        "scenario_ids": ids,
        "scenario_definitions": definitions,
        "metrics": list(metrics),
    }


def test_slippage_stress_compares_predeclared_scenarios_without_verdict():
    definitions = {
        "base": {"entry_bps": "2", "exit_bps": "2"},
        "triple": {"entry_bps": "6", "exit_bps": "6"},
    }
    control = _artifact([
        _scenario("base", definitions["base"], 100, "1.0"),
        _scenario("triple", definitions["triple"], 60, "0.6"),
    ])
    variant = _artifact([
        _scenario("base", definitions["base"], 120, "1.1"),
        _scenario("triple", definitions["triple"], 75, "0.7"),
    ])

    result = SlippageStressStageEvaluator().evaluate(
        stage_protocol=_protocol(["base", "triple"], definitions),
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.slippage-stress-evaluation.v1"
    assert result["scenarios"]["triple"]["metrics"]["total_pnl"]["delta"] == "15"
    assert result["scenarios"]["base"]["slippage_assumptions"] == definitions["base"]
    assert "winner" not in result
    assert "pass" not in result


def test_slippage_stress_requires_predeclared_scenarios_definitions_and_metrics():
    definitions = {"base": {"entry_bps": "2", "exit_bps": "2"}}
    evidence = _artifact([_scenario("base", definitions["base"], 100)])
    evaluator = SlippageStressStageEvaluator()

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_SCENARIOS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"scenario_definitions": definitions, "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_DEFINITIONS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_METRICS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"scenario_ids": ["base"], "scenario_definitions": definitions},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_slippage_stress_rejects_tampered_evidence():
    definitions = {"base": {"entry_bps": "2", "exit_bps": "2"}}
    evidence = _artifact([_scenario("base", definitions["base"], 100)])
    evidence["scenarios"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_EVIDENCE_FINGERPRINT_MISMATCH"):
        SlippageStressStageEvaluator().evaluate(
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            control_evidence=evidence,
            variant_evidence=_artifact([_scenario("base", definitions["base"], 100)]),
        )


def test_slippage_stress_rejects_scenario_identity_drift():
    definitions = {"base": {"entry_bps": "2", "exit_bps": "2"}}
    control = _artifact([_scenario("base", definitions["base"], 100)])
    variant = _artifact([_scenario("other", definitions["base"], 100)])

    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_VARIANT_SCENARIOS_MISMATCH"):
        SlippageStressStageEvaluator().evaluate(
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            control_evidence=control,
            variant_evidence=variant,
        )


def test_slippage_stress_rejects_assumption_drift_from_protocol():
    definitions = {"base": {"entry_bps": "2", "exit_bps": "2"}}
    control = _artifact([_scenario("base", definitions["base"], 100)])
    variant = _artifact([_scenario("base", {"entry_bps": "3", "exit_bps": "2"}, 100)])

    with pytest.raises(
        ValueError,
        match="SLIPPAGE_STRESS_PREDECLARED_DEFINITION_MISMATCH:base",
    ):
        SlippageStressStageEvaluator().evaluate(
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            control_evidence=control,
            variant_evidence=variant,
        )
