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


def test_slippage_stress_producer_projects_predeclared_scenarios_and_metrics():
    repository = _Repository()
    record = SlippageStressEvidenceProducer(repository).produce(
        uuid4(),
        stage_protocol={
            "scenario_ids": ["base", "triple"],
            "metrics": ["total_pnl", "sharpe"],
        },
        scenarios=[
            {
                "scenario_id": "base",
                "slippage_assumptions": {"entry_bps": "2", "exit_bps": "2"},
                "certified_result": _certified("10", "1.0"),
            },
            {
                "scenario_id": "triple",
                "slippage_assumptions": {"entry_bps": "6", "exit_bps": "6"},
                "certified_result": _certified("6", "0.6"),
            },
        ],
    )

    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.slippage-stress-evidence.v1"
    assert artifact["scenarios"][1] == {
        "scenario_id": "triple",
        "slippage_assumptions": {"entry_bps": "6", "exit_bps": "6"},
        "metrics": {"total_pnl": "6", "sharpe": "0.6"},
    }
    assert artifact["result_fingerprint"] == configuration_hash(artifact["scenarios"])


def test_slippage_stress_producer_rejects_noncertified_result():
    repository = _Repository()
    with pytest.raises(
        ValueError,
        match="SLIPPAGE_STRESS_CERTIFIED_RESULT_REQUIRED:base",
    ):
        SlippageStressEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            scenarios=[
                {
                    "scenario_id": "base",
                    "slippage_assumptions": {"entry_bps": "2", "exit_bps": "2"},
                    "certified_result": {"schema": "hope.backtest-result.v1"},
                }
            ],
        )
    assert repository.persisted == []


def test_slippage_stress_producer_rejects_scenario_identity_or_order_drift():
    repository = _Repository()
    with pytest.raises(
        ValueError,
        match="SLIPPAGE_STRESS_SOURCE_SCENARIOS_MISMATCH",
    ):
        SlippageStressEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={
                "scenario_ids": ["base", "triple"],
                "metrics": ["total_pnl"],
            },
            scenarios=[
                {
                    "scenario_id": "triple",
                    "slippage_assumptions": {"entry_bps": "6", "exit_bps": "6"},
                    "certified_result": _certified(),
                },
                {
                    "scenario_id": "base",
                    "slippage_assumptions": {"entry_bps": "2", "exit_bps": "2"},
                    "certified_result": _certified(),
                },
            ],
        )
    assert repository.persisted == []


def test_slippage_stress_producer_rejects_fee_fields():
    repository = _Repository()
    with pytest.raises(
        ValueError,
        match="SLIPPAGE_STRESS_COST_ASSUMPTION_FORBIDDEN:base",
    ):
        SlippageStressEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={"scenario_ids": ["base"], "metrics": ["total_pnl"]},
            scenarios=[
                {
                    "scenario_id": "base",
                    "slippage_assumptions": {
                        "entry_bps": "2",
                        "exit_bps": "2",
                        "commission_bps": "1",
                    },
                    "certified_result": _certified(),
                }
            ],
        )
    assert repository.persisted == []
