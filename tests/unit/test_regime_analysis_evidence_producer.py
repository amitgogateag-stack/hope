from uuid import uuid4

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.regime_analysis_evidence import (
    RegimeAnalysisEvidenceProducer,
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


def test_regime_producer_projects_only_predeclared_metrics_and_persists():
    repository = _Repository()
    record = RegimeAnalysisEvidenceProducer(repository).produce(
        uuid4(),
        stage_protocol={
            "regime_ids": ["trend", "range"],
            "metrics": ["total_pnl", "sharpe"],
        },
        regimes=[
            {"regime_id": "trend", "certified_result": _certified("20", "1.1")},
            {"regime_id": "range", "certified_result": _certified("-5", "0.2")},
        ],
    )

    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.regime-analysis-evidence.v1"
    assert artifact["regimes"] == [
        {"regime_id": "trend", "metrics": {"total_pnl": "20", "sharpe": "1.1"}},
        {"regime_id": "range", "metrics": {"total_pnl": "-5", "sharpe": "0.2"}},
    ]
    assert artifact["result_fingerprint"] == configuration_hash(artifact["regimes"])


def test_regime_producer_rejects_noncertified_source_result():
    repository = _Repository()
    with pytest.raises(
        ValueError,
        match="REGIME_ANALYSIS_CERTIFIED_RESULT_REQUIRED:trend",
    ):
        RegimeAnalysisEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={"regime_ids": ["trend"], "metrics": ["total_pnl"]},
            regimes=[
                {
                    "regime_id": "trend",
                    "certified_result": {"schema": "hope.backtest-result.v1"},
                }
            ],
        )
    assert repository.persisted == []


def test_regime_producer_rejects_regime_identity_or_order_drift():
    repository = _Repository()
    with pytest.raises(ValueError, match="REGIME_ANALYSIS_SOURCE_REGIMES_MISMATCH"):
        RegimeAnalysisEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={
                "regime_ids": ["trend", "range"],
                "metrics": ["total_pnl"],
            },
            regimes=[
                {"regime_id": "range", "certified_result": _certified()},
                {"regime_id": "trend", "certified_result": _certified()},
            ],
        )
    assert repository.persisted == []


def test_regime_producer_rejects_unpredeclared_metric():
    repository = _Repository()
    with pytest.raises(ValueError, match="REGIME_ANALYSIS_METRIC_INVALID"):
        RegimeAnalysisEvidenceProducer(repository).produce(
            uuid4(),
            stage_protocol={
                "regime_ids": ["trend"],
                "metrics": ["made_up_metric"],
            },
            regimes=[
                {"regime_id": "trend", "certified_result": _certified()},
            ],
        )
    assert repository.persisted == []
