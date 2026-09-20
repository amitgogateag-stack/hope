from uuid import uuid4

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.cost_stress_evidence import CostStressEvidenceProducer


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


def test_cost_stress_producer_resolves_durable_sources_and_projects_scenarios():
    repository = _Repository()
    base_run, double_run = uuid4(), uuid4()
    resolver = _SourceResolver({
        base_run: _certified("10", "1.0"),
        double_run: _certified("7", "0.7"),
    })
    record = CostStressEvidenceProducer(repository, resolver).produce(
        uuid4(),
        stage_protocol={
            "scenario_ids": ["base", "double"],
            "metrics": ["total_pnl", "sharpe"],
        },
        scenarios=[
            {
                "scenario_id": "base",
                "cost_assumptions": {"commission_bps": "2", "fees_bps": "1"},
                "source_research_run_id": base_run,
            },
            {
                "scenario_id": "double",
                "cost_assumptions": {"commission_bps": "4", "fees_bps": "2"},
                "source_research_run_id": double_run,
            },
        ],
    )

    assert resolver.resolved == [base_run, double_run]
    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.cost-stress-evidence.v1"
    assert artifact["scenarios"][1] == {
        "scenario_id": "double",
        "source_research_run_id": str(double_run),
        "cost_assumptions": {"commission_bps": "4", "fees_bps": "2"},
        "metrics": {"total_pnl": "7", "sharpe": "0.7"},
    }
    assert artifact["result_fingerprint"] == configuration_hash(artifact["scenarios"])


def test_cost_stress_producer_requires_source_run_id():
    repository = _Repository()
    resolver = _SourceResolver({})
    with pytest.raises(ValueError, match="COST_STRESS_SOURCE_RUN_ID_REQUIRED:base"):
        CostStressEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            scenarios=[
                {
                    "scenario_id": "base",
                    "cost_assumptions": {"commission_bps": "2"},
                }
            ],
        )
    assert resolver.resolved == []
    assert repository.persisted == []


def test_cost_stress_producer_propagates_verified_source_failure():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({})
    with pytest.raises(ValueError, match="VERIFIED_RESEARCH_SOURCE_RUN_MISSING"):
        CostStressEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            scenarios=[
                {
                    "scenario_id": "base",
                    "cost_assumptions": {"commission_bps": "2"},
                    "source_research_run_id": source_run_id,
                }
            ],
        )
    assert repository.persisted == []


def test_cost_stress_producer_rejects_scenario_identity_or_order_drift():
    repository = _Repository()
    first, second = uuid4(), uuid4()
    resolver = _SourceResolver({first: _certified(), second: _certified()})
    with pytest.raises(ValueError, match="COST_STRESS_SOURCE_SCENARIOS_MISMATCH"):
        CostStressEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "scenario_ids": ["base", "double"],
                "metrics": ["total_pnl"],
            },
            scenarios=[
                {
                    "scenario_id": "double",
                    "cost_assumptions": {"commission_bps": "4"},
                    "source_research_run_id": first,
                },
                {
                    "scenario_id": "base",
                    "cost_assumptions": {"commission_bps": "2"},
                    "source_research_run_id": second,
                },
            ],
        )
    assert repository.persisted == []


def test_cost_stress_producer_rejects_slippage_in_cost_assumptions():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({source_run_id: _certified()})
    with pytest.raises(
        ValueError,
        match="COST_STRESS_SLIPPAGE_ASSUMPTION_FORBIDDEN:base",
    ):
        CostStressEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            scenarios=[
                {
                    "scenario_id": "base",
                    "cost_assumptions": {
                        "commission_bps": "2",
                        "slippage_bps": "3",
                    },
                    "source_research_run_id": source_run_id,
                }
            ],
        )
    assert repository.persisted == []
