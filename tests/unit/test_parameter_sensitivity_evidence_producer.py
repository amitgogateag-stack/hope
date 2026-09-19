from uuid import uuid4

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.parameter_sensitivity_evidence import (
    ParameterSensitivityEvidenceProducer,
)


class _Repository:
    def __init__(self):
        self.persisted = []

    def persist(self, record):
        self.persisted.append(record)
        return True


def _certified(pnl="10", sharpe="1.0"):
    return {
        "schema": "hope.certified-backtest-result.v3",
        "execution_provenance": {},
        "research_provenance": {},
        "backtest": {
            "schema": "hope.backtest-result.v1",
            "metrics": {
                "initial_equity": "100",
                "final_equity": "110",
                "total_pnl": pnl,
                "total_return": "0.1",
                "max_drawdown": "0.02",
                "volatility": "0.1",
                "sharpe": sharpe,
                "downside_deviation": "0.05",
                "sortino": "1.2",
            },
        },
    }


def test_parameter_sensitivity_producer_projects_declared_sets_and_metrics():
    repository = _Repository()
    record = ParameterSensitivityEvidenceProducer(repository).produce(
        uuid4(),
        stage_protocol={
            "parameter_set_ids": ["low", "base", "high"],
            "metrics": ["total_pnl", "sharpe"],
        },
        parameter_sets=[
            {
                "parameter_set_id": "low",
                "parameters": {"lookback": 10},
                "certified_result": _certified("8", "0.7"),
            },
            {
                "parameter_set_id": "base",
                "parameters": {"lookback": 20},
                "certified_result": _certified("10", "1.0"),
            },
            {
                "parameter_set_id": "high",
                "parameters": {"lookback": 30},
                "certified_result": _certified("9", "0.8"),
            },
        ],
    )

    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.parameter-sensitivity-evidence.v1"
    assert artifact["parameter_sets"][1] == {
        "parameter_set_id": "base",
        "parameters": {"lookback": 20},
        "metrics": {"total_pnl": "10", "sharpe": "1.0"},
    }
    assert artifact["result_fingerprint"] == configuration_hash(
        artifact["parameter_sets"]
    )
    assert "best" not in artifact
    assert "winner" not in artifact


def test_parameter_sensitivity_producer_rejects_noncertified_source_result():
    repository = _Repository()
    with pytest.raises(
        ValueError,
        match="PARAMETER_SENSITIVITY_CERTIFIED_RESULT_REQUIRED:base",
    ):
        ParameterSensitivityEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={
                "parameter_set_ids": ["base"],
                "metrics": ["total_pnl"],
            },
            parameter_sets=[
                {
                    "parameter_set_id": "base",
                    "parameters": {"lookback": 20},
                    "certified_result": {"schema": "hope.backtest-result.v1"},
                }
            ],
        )
    assert repository.persisted == []


def test_parameter_sensitivity_producer_rejects_identity_or_order_drift():
    repository = _Repository()
    with pytest.raises(
        ValueError,
        match="PARAMETER_SENSITIVITY_SOURCE_PARAMETER_SETS_MISMATCH",
    ):
        ParameterSensitivityEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={
                "parameter_set_ids": ["low", "high"],
                "metrics": ["total_pnl"],
            },
            parameter_sets=[
                {
                    "parameter_set_id": "high",
                    "parameters": {"lookback": 30},
                    "certified_result": _certified(),
                },
                {
                    "parameter_set_id": "low",
                    "parameters": {"lookback": 10},
                    "certified_result": _certified(),
                },
            ],
        )
    assert repository.persisted == []


def test_parameter_sensitivity_producer_requires_explicit_parameter_definition():
    repository = _Repository()
    with pytest.raises(
        ValueError,
        match="PARAMETER_SENSITIVITY_PARAMETER_SET_INVALID",
    ):
        ParameterSensitivityEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={
                "parameter_set_ids": ["base"],
                "metrics": ["total_pnl"],
            },
            parameter_sets=[
                {
                    "parameter_set_id": "base",
                    "parameters": {},
                    "certified_result": _certified(),
                }
            ],
        )
    assert repository.persisted == []
