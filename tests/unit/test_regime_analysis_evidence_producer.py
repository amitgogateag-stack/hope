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


def test_regime_producer_resolves_durable_sources_projects_metrics_and_persists():
    repository = _Repository()
    trend_run, range_run = uuid4(), uuid4()
    resolver = _SourceResolver({
        trend_run: _certified("20", "1.1"),
        range_run: _certified("-5", "0.2"),
    })
    record = RegimeAnalysisEvidenceProducer(repository, resolver).produce(
        uuid4(),
        stage_protocol={
            "regime_ids": ["trend", "range"],
            "metrics": ["total_pnl", "sharpe"],
        },
        regimes=[
            {"regime_id": "trend", "source_research_run_id": trend_run},
            {"regime_id": "range", "source_research_run_id": range_run},
        ],
    )

    assert resolver.resolved == [trend_run, range_run]
    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.regime-analysis-evidence.v1"
    assert artifact["regimes"] == [
        {
            "regime_id": "trend",
            "source_research_run_id": str(trend_run),
            "metrics": {"total_pnl": "20", "sharpe": "1.1"},
        },
        {
            "regime_id": "range",
            "source_research_run_id": str(range_run),
            "metrics": {"total_pnl": "-5", "sharpe": "0.2"},
        },
    ]
    assert artifact["result_fingerprint"] == configuration_hash(artifact["regimes"])


def test_regime_producer_rejects_missing_source_run_id_before_resolution():
    repository = _Repository()
    resolver = _SourceResolver({})
    with pytest.raises(
        ValueError,
        match="REGIME_ANALYSIS_SOURCE_RUN_ID_REQUIRED:trend",
    ):
        RegimeAnalysisEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={"regime_ids": ["trend"], "metrics": ["total_pnl"]},
            regimes=[{"regime_id": "trend"}],
        )
    assert resolver.resolved == []
    assert repository.persisted == []


def test_regime_producer_propagates_verified_source_failure():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({})
    with pytest.raises(ValueError, match="VERIFIED_RESEARCH_SOURCE_RUN_MISSING"):
        RegimeAnalysisEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={"regime_ids": ["trend"], "metrics": ["total_pnl"]},
            regimes=[{"regime_id": "trend", "source_research_run_id": source_run_id}],
        )
    assert repository.persisted == []


def test_regime_producer_rejects_regime_identity_or_order_drift():
    repository = _Repository()
    first, second = uuid4(), uuid4()
    resolver = _SourceResolver({first: _certified(), second: _certified()})
    with pytest.raises(ValueError, match="REGIME_ANALYSIS_SOURCE_REGIMES_MISMATCH"):
        RegimeAnalysisEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "regime_ids": ["trend", "range"],
                "metrics": ["total_pnl"],
            },
            regimes=[
                {"regime_id": "range", "source_research_run_id": first},
                {"regime_id": "trend", "source_research_run_id": second},
            ],
        )
    assert repository.persisted == []


def test_regime_producer_rejects_unpredeclared_metric():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({source_run_id: _certified()})
    with pytest.raises(ValueError, match="REGIME_ANALYSIS_METRIC_INVALID"):
        RegimeAnalysisEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "regime_ids": ["trend"],
                "metrics": ["made_up_metric"],
            },
            regimes=[
                {"regime_id": "trend", "source_research_run_id": source_run_id},
            ],
        )
    assert repository.persisted == []
