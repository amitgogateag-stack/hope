from uuid import uuid4

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.slippage_stress_evidence import (
    SlippageStressEvidenceProducer,
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


def _protocol(ids, definitions, metrics=("total_pnl", "sharpe")):
    return {
        "scenario_ids": ids,
        "scenario_definitions": definitions,
        "metrics": list(metrics),
    }


def test_slippage_stress_producer_resolves_durable_sources_and_projects_scenarios():
    repository = _Repository()
    definitions = {
        "base": {"entry_bps": "2", "exit_bps": "2"},
        "triple": {"entry_bps": "6", "exit_bps": "6"},
    }
    base_run, triple_run = uuid4(), uuid4()
    resolver = _SourceResolver({
        base_run: _certified("10", "1.0"),
        triple_run: _certified("6", "0.6"),
    })
    record = SlippageStressEvidenceProducer(repository, resolver).produce(
        uuid4(),
        stage_protocol=_protocol(["base", "triple"], definitions),
        scenarios=[
            {"scenario_id": "base", "slippage_assumptions": definitions["base"], "source_research_run_id": base_run},
            {"scenario_id": "triple", "slippage_assumptions": definitions["triple"], "source_research_run_id": triple_run},
        ],
    )

    assert resolver.resolved == [base_run, triple_run]
    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["scenarios"][1]["slippage_assumptions"] == definitions["triple"]
    assert artifact["scenarios"][1]["source_research_run_id"] == str(triple_run)
    assert artifact["result_fingerprint"] == configuration_hash(artifact["scenarios"])


def test_slippage_stress_producer_requires_predeclared_definitions():
    repository = _Repository()
    resolver = _SourceResolver({})
    with pytest.raises(ValueError, match="SLIPPAGE_STRESS_DEFINITIONS_PREDECLARATION_REQUIRED"):
        SlippageStressEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            scenarios=[],
        )


def test_slippage_stress_producer_rejects_definition_drift_before_resolution():
    repository = _Repository()
    source_run_id = uuid4()
    definitions = {"base": {"entry_bps": "2", "exit_bps": "2"}}
    resolver = _SourceResolver({source_run_id: _certified()})
    with pytest.raises(
        ValueError,
        match="SLIPPAGE_STRESS_PREDECLARED_DEFINITION_MISMATCH:base",
    ):
        SlippageStressEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            scenarios=[{
                "scenario_id": "base",
                "slippage_assumptions": {"entry_bps": "3", "exit_bps": "2"},
                "source_research_run_id": source_run_id,
            }],
        )
    assert resolver.resolved == []
    assert repository.persisted == []


def test_slippage_stress_producer_propagates_verified_source_failure():
    repository = _Repository()
    source_run_id = uuid4()
    definitions = {"base": {"entry_bps": "2", "exit_bps": "2"}}
    resolver = _SourceResolver({})
    with pytest.raises(ValueError, match="VERIFIED_RESEARCH_SOURCE_RUN_MISSING"):
        SlippageStressEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            scenarios=[{
                "scenario_id": "base",
                "slippage_assumptions": definitions["base"],
                "source_research_run_id": source_run_id,
            }],
        )
    assert repository.persisted == []


def test_slippage_stress_producer_rejects_fee_fields_in_predeclared_assumptions():
    repository = _Repository()
    source_run_id = uuid4()
    definitions = {
        "base": {"entry_bps": "2", "exit_bps": "2", "commission_bps": "1"}
    }
    resolver = _SourceResolver({source_run_id: _certified()})
    with pytest.raises(
        ValueError,
        match="SLIPPAGE_STRESS_COST_ASSUMPTION_FORBIDDEN:base",
    ):
        SlippageStressEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol=_protocol(["base"], definitions, ("total_pnl",)),
            scenarios=[{
                "scenario_id": "base",
                "slippage_assumptions": definitions["base"],
                "source_research_run_id": source_run_id,
            }],
        )
