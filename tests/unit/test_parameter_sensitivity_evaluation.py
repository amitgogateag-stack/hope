import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.parameter_sensitivity import (
    PARAMETER_SENSITIVITY_EVIDENCE_SCHEMA,
    ParameterSensitivityStageEvaluator,
)


def _set(parameter_set_id, parameters, pnl, sharpe="1.0"):
    return {
        "parameter_set_id": parameter_set_id,
        "parameters": parameters,
        "metrics": {"total_pnl": str(pnl), "sharpe": str(sharpe)},
    }


def _artifact(parameter_sets):
    return {
        "schema": PARAMETER_SENSITIVITY_EVIDENCE_SCHEMA,
        "parameter_sets": parameter_sets,
        "result_fingerprint": configuration_hash(parameter_sets),
    }


def _protocol(ids, definitions, metrics=("total_pnl", "sharpe")):
    return {
        "parameter_set_ids": ids,
        "parameter_definitions": definitions,
        "metrics": list(metrics),
    }


def test_parameter_sensitivity_compares_predeclared_sets_without_optimum():
    definitions = {
        "low": {"lookback": 10},
        "base": {"lookback": 20},
        "high": {"lookback": 30},
    }
    control = _artifact([
        _set("low", definitions["low"], 100, "0.8"),
        _set("base", definitions["base"], 120, "1.0"),
        _set("high", definitions["high"], 90, "0.7"),
    ])
    variant = _artifact([
        _set("low", definitions["low"], 105, "0.85"),
        _set("base", definitions["base"], 130, "1.1"),
        _set("high", definitions["high"], 95, "0.75"),
    ])

    result = ParameterSensitivityStageEvaluator().evaluate(
        stage_protocol=_protocol(["low", "base", "high"], definitions),
        control_evidence=control,
        variant_evidence=variant,
    )

    assert result["schema"] == "hope.parameter-sensitivity-evaluation.v1"
    assert result["parameter_sets"]["base"]["metrics"]["total_pnl"]["delta"] == "10"
    assert result["parameter_sets"]["base"]["parameters"] == {"lookback": 20}
    assert "winner" not in result
    assert "best" not in result
    assert "pass" not in result


def test_parameter_sensitivity_requires_predeclared_sets_definitions_and_metrics():
    definitions = {"base": {"lookback": 20}}
    evidence = _artifact([_set("base", definitions["base"], 100)])
    evaluator = ParameterSensitivityStageEvaluator()

    with pytest.raises(ValueError, match="PARAMETER_SENSITIVITY_PARAMETER_SETS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"parameter_definitions": definitions, "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="PARAMETER_SENSITIVITY_DEFINITIONS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"parameter_set_ids": ["base"], "metrics": ["total_pnl"]},
            control_evidence=evidence,
            variant_evidence=evidence,
        )

    with pytest.raises(ValueError, match="PARAMETER_SENSITIVITY_METRICS_PREDECLARATION_REQUIRED"):
        evaluator.evaluate(
            stage_protocol={"parameter_set_ids": ["base"], "parameter_definitions": definitions},
            control_evidence=evidence,
            variant_evidence=evidence,
        )


def test_parameter_sensitivity_rejects_tampered_evidence():
    definitions = {"base": {"lookback": 20}}
    evidence = _artifact([_set("base", definitions["base"], 100)])
    evidence["parameter_sets"][0]["metrics"]["total_pnl"] = "999"

    with pytest.raises(ValueError, match="PARAMETER_SENSITIVITY_EVIDENCE_FINGERPRINT_MISMATCH"):
        ParameterSensitivityStageEvaluator().evaluate(
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            control_evidence=evidence,
            variant_evidence=_artifact([_set("base", definitions["base"], 100)]),
        )


def test_parameter_sensitivity_rejects_parameter_identity_drift():
    definitions = {"base": {"lookback": 20}}
    control = _artifact([_set("base", definitions["base"], 100)])
    variant = _artifact([_set("other", definitions["base"], 100)])

    with pytest.raises(ValueError, match="PARAMETER_SENSITIVITY_VARIANT_PARAMETER_SETS_MISMATCH"):
        ParameterSensitivityStageEvaluator().evaluate(
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            control_evidence=control,
            variant_evidence=variant,
        )


def test_parameter_sensitivity_rejects_parameter_definition_drift_from_protocol():
    definitions = {"base": {"lookback": 20}}
    control = _artifact([_set("base", definitions["base"], 100)])
    variant = _artifact([_set("base", {"lookback": 21}, 100)])

    with pytest.raises(
        ValueError,
        match="PARAMETER_SENSITIVITY_PREDECLARED_DEFINITION_MISMATCH:base",
    ):
        ParameterSensitivityStageEvaluator().evaluate(
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            control_evidence=control,
            variant_evidence=variant,
        )
