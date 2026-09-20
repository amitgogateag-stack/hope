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


class _SourceResolver:
    def __init__(self, evidence_by_run_id):
        self.evidence_by_run_id = evidence_by_run_id
        self.resolved = []

    def resolve(self, run_id):
        self.resolved.append(run_id)
        if run_id not in self.evidence_by_run_id:
            raise ValueError("VERIFIED_RESEARCH_SOURCE_RUN_MISSING")
        return self.evidence_by_run_id[run_id]


def _certified(pnl="10", sharpe="1.0"):
    return {
        "schema": "hope.certified-backtest-result.v3",
        "execution_provenance": {"verified": True},
        "research_provenance": {"verified": True},
        "backtest": {
            "schema": "hope.backtest-result.v1",
            "result": {
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
                }
            },
        },
    }


def test_parameter_sensitivity_producer_resolves_durable_sources_and_projects_sets():
    repository = _Repository()
    low_run, base_run, high_run = uuid4(), uuid4(), uuid4()
    resolver = _SourceResolver({
        low_run: _certified("8", "0.7"),
        base_run: _certified("10", "1.0"),
        high_run: _certified("9", "0.8"),
    })
    record = ParameterSensitivityEvidenceProducer(repository, resolver).produce(
        uuid4(),
        stage_protocol={
            "parameter_set_ids": ["low", "base", "high"],
            "metrics": ["total_pnl", "sharpe"],
        },
        parameter_sets=[
            {
                "parameter_set_id": "low",
                "parameters": {"lookback": 10},
                "source_research_run_id": low_run,
            },
            {
                "parameter_set_id": "base",
                "parameters": {"lookback": 20},
                "source_research_run_id": base_run,
            },
            {
                "parameter_set_id": "high",
                "parameters": {"lookback": 30},
                "source_research_run_id": high_run,
            },
        ],
    )

    assert resolver.resolved == [low_run, base_run, high_run]
    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.parameter-sensitivity-evidence.v1"
    assert artifact["parameter_sets"][1] == {
        "parameter_set_id": "base",
        "source_research_run_id": str(base_run),
        "parameters": {"lookback": 20},
        "metrics": {"total_pnl": "10", "sharpe": "1.0"},
    }
    assert artifact["result_fingerprint"] == configuration_hash(
        artifact["parameter_sets"]
    )
    assert "best" not in artifact
    assert "winner" not in artifact


def test_parameter_sensitivity_producer_requires_source_run_id():
    repository = _Repository()
    resolver = _SourceResolver({})
    with pytest.raises(
        ValueError,
        match="PARAMETER_SENSITIVITY_SOURCE_RUN_ID_REQUIRED:base",
    ):
        ParameterSensitivityEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "parameter_set_ids": ["base"],
                "metrics": ["total_pnl"],
            },
            parameter_sets=[
                {
                    "parameter_set_id": "base",
                    "parameters": {"lookback": 20},
                }
            ],
        )
    assert resolver.resolved == []
    assert repository.persisted == []


def test_parameter_sensitivity_producer_propagates_verified_source_failure():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({})
    with pytest.raises(ValueError, match="VERIFIED_RESEARCH_SOURCE_RUN_MISSING"):
        ParameterSensitivityEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "parameter_set_ids": ["base"],
                "metrics": ["total_pnl"],
            },
            parameter_sets=[
                {
                    "parameter_set_id": "base",
                    "parameters": {"lookback": 20},
                    "source_research_run_id": source_run_id,
                }
            ],
        )
    assert repository.persisted == []


def test_parameter_sensitivity_producer_rejects_identity_or_order_drift():
    repository = _Repository()
    first, second = uuid4(), uuid4()
    resolver = _SourceResolver({first: _certified(), second: _certified()})
    with pytest.raises(
        ValueError,
        match="PARAMETER_SENSITIVITY_SOURCE_PARAMETER_SETS_MISMATCH",
    ):
        ParameterSensitivityEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "parameter_set_ids": ["low", "high"],
                "metrics": ["total_pnl"],
            },
            parameter_sets=[
                {
                    "parameter_set_id": "high",
                    "parameters": {"lookback": 30},
                    "source_research_run_id": first,
                },
                {
                    "parameter_set_id": "low",
                    "parameters": {"lookback": 10},
                    "source_research_run_id": second,
                },
            ],
        )
    assert repository.persisted == []


def test_parameter_sensitivity_producer_requires_explicit_parameter_definition():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({source_run_id: _certified()})
    with pytest.raises(
        ValueError,
        match="PARAMETER_SENSITIVITY_PARAMETER_SET_INVALID",
    ):
        ParameterSensitivityEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "parameter_set_ids": ["base"],
                "metrics": ["total_pnl"],
            },
            parameter_sets=[
                {
                    "parameter_set_id": "base",
                    "parameters": {},
                    "source_research_run_id": source_run_id,
                }
            ],
        )
    assert repository.persisted == []
