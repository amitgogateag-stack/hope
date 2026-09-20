import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.cost_stress import (
    COST_STRESS_EVIDENCE_SCHEMA,
    CostStressStageEvaluator,
)


def _scenario(scenario_id, assumptions, pnl, sharpe="1.0"):
    return {
        "scenario_id": scenario_id,
        "cost_assumptions": assumptions,
        "metrics": {"total_pnl": str(pnl), "sharpe": str(sharpe)},
    }


def _artifact(scenarios):
    return {
        "schema": COST_STRESS_EVIDENCE_SCHEMA,
        "scenarios": scenarios,
        "result_fingerprint": configuration_hash(scenarios),
    }


def _protocol(ids, definitions, metrics=("total_pnl", "sharpe")):
    return {
        "scenario_ids": ids,
        "scenario_definitions": definitions,
        "metrics": list(metrics),
    }


def test_cost_stress_compares_predeclared_scenarios_without_verdict():
    definitions = {
        "base": {"commission_bps": "2", "fees_bps": "1"},
        "double": {"commission_bps": "4", "fees_bps": "2"},
    }
    control = _artifact([
        _scenario("base", definitions["base"], 100, "1.0"),
        _scenario("double", definitions["double"], 70, "0.7"),
    ])
    variant = _artifact([
        _scenario("base", definitions["base"], 120, "1.1"),
        _scenario("double", definitions["double"], 80, "0.8"),
    ])

    result = CostStressStageEvaluator().evaluate(
        stage_protocol=_protocol(["base", "double"], definitions),
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.cost-stress-evaluation.v1"
    assert result["scenarios"]["double"]["metrics"]["total_pnl"]["delta"] == "10"
    assert result["scenarios"]["base"]["cost_assumptions"] == definitions["base"]
    assert "winner" not in result
    assert "pass" not in result


def test_cost_stress_requires_predeclared_scenarios_definitions_and_metrics():
    definitions = {"base": {"commission_bps": "2"}}
    evidence = _artifact([_scenario("base", definitions["base"], 100)])
    evaluator = CostStressStageEvaluator()

    with pytest.raises(ValueError, match="COST_STRESS_SCENARIOS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"scenario_definitions": definitions, "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="COST_STRESS_DEFINITIONS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="COST_STRESS_METRICS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"scenario_ids": ["base"], "scenario_definitions": definitions},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_cost_stress_rejects_tampered_evidence():
    definitions = {"base": {"commission_bps": "2"}}
    evidence = _artifact([_scenario("base", definitions["base"], 100)])
    evidence["scenarios"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(ValueError, match="COST_STRESS_EVIDENCE_FINGERPRINT_MISMATCH"):
        CostStressStageEvaluator().evaluate(
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            control_evidence=evidence,
            variant_evidence=_artifact([_scenario("base", definitions["base"], 100)]),
        )


def test_cost_stress_rejects_scenario_identity_drift():
    definitions = {"base": {"commission_bps": "2"}}
    control = _artifact([_scenario("base", definitions["base"], 100)])
    variant = _artifact([_scenario("other", definitions["base"], 100)])

    with pytest.raises(ValueError, match="COST_STRESS_VARIANT_SCENARIOS_MISMATCH"):
        CostStressStageEvaluator().evaluate(
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            control_evidence=control,
            variant_evidence=variant,
        )


def test_cost_stress_rejects_assumption_drift_from_protocol():
    definitions = {"base": {"commission_bps": "2"}}
    control = _artifact([_scenario("base", definitions["base"], 100)])
    variant = _artifact([_scenario("base", {"commission_bps": "3"}, 100)])

    with pytest.raises(
        ValueError,
        match="COST_STRESS_PREDECLARED_DEFINITION_MISMATCH:base",
    ):
        CostStressStageEvaluator().evaluate(
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            control_evidence=control,
            variant_evidence=variant,
        )
