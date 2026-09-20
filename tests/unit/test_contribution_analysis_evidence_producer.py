from uuid import uuid4

import pytest

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.contribution_analysis_evidence import (
    ContributionAnalysisEvidenceProducer,
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


def test_contribution_producer_resolves_sources_and_requires_predeclared_definitions():
    repository = _Repository()
    definitions = {
        "large_cap": {"dimension": "market_cap_bucket", "bucket": "large"},
        "mid_cap": {"dimension": "market_cap_bucket", "bucket": "mid"},
    }
    large_run, mid_run = uuid4(), uuid4()
    resolver = _SourceResolver({
        large_run: _certified("12", "1.1"),
        mid_run: _certified("8", "0.7"),
    })
    record = ContributionAnalysisEvidenceProducer(repository, resolver).produce(
        uuid4(),
        stage_protocol={
            "contribution_ids": ["large_cap", "mid_cap"],
            "contribution_definitions": definitions,
            "metrics": ["total_pnl", "sharpe"],
        },
        contributions=[
            {
                "contribution_id": "large_cap",
                "contribution_definition": definitions["large_cap"],
                "source_research_run_id": large_run,
            },
            {
                "contribution_id": "mid_cap",
                "contribution_definition": definitions["mid_cap"],
                "source_research_run_id": mid_run,
            },
        ],
    )

    assert resolver.resolved == [large_run, mid_run]
    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.contribution-analysis-evidence.v1"
    assert artifact["contributions"][0]["source_research_run_id"] == str(large_run)
    assert artifact["contributions"][0]["contribution_definition"] == definitions["large_cap"]
    assert artifact["result_fingerprint"] == configuration_hash(
        artifact["contributions"]
    )
    assert "winner" not in artifact
    assert "best" not in artifact


def test_contribution_producer_requires_source_run_id():
    repository = _Repository()
    resolver = _SourceResolver({})
    definitions = {
        "large_cap": {"dimension": "market_cap_bucket", "bucket": "large"},
    }
    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_SOURCE_RUN_ID_REQUIRED:large_cap",
    ):
        ContributionAnalysisEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "contribution_ids": ["large_cap"],
                "contribution_definitions": definitions,
                "metrics": ["total_pnl"],
            },
            contributions=[
                {
                    "contribution_id": "large_cap",
                    "contribution_definition": definitions["large_cap"],
                }
            ],
        )
    assert resolver.resolved == []
    assert repository.persisted == []


def test_contribution_producer_propagates_verified_source_failure():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({})
    definitions = {
        "large_cap": {"dimension": "market_cap_bucket", "bucket": "large"},
    }
    with pytest.raises(ValueError, match="VERIFIED_RESEARCH_SOURCE_RUN_MISSING"):
        ContributionAnalysisEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "contribution_ids": ["large_cap"],
                "contribution_definitions": definitions,
                "metrics": ["total_pnl"],
            },
            contributions=[
                {
                    "contribution_id": "large_cap",
                    "contribution_definition": definitions["large_cap"],
                    "source_research_run_id": source_run_id,
                }
            ],
        )
    assert repository.persisted == []


def test_contribution_producer_rejects_retrospective_rank_definition():
    repository = _Repository()
    resolver = _SourceResolver({})
    definitions = {
        "top_decile": {"dimension": "symbol_rank", "bucket": "top_decile"},
    }
    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_RETROSPECTIVE_DEFINITION_FORBIDDEN:top_decile",
    ):
        ContributionAnalysisEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "contribution_ids": ["top_decile"],
                "contribution_definitions": definitions,
                "metrics": ["total_pnl"],
            },
            contributions=[],
        )
    assert repository.persisted == []


def test_contribution_producer_rejects_definition_drift_from_protocol():
    repository = _Repository()
    source_run_id = uuid4()
    resolver = _SourceResolver({source_run_id: _certified()})
    definitions = {
        "large_cap": {"dimension": "market_cap_bucket", "bucket": "large"},
    }
    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_PREDECLARED_DEFINITION_MISMATCH:large_cap",
    ):
        ContributionAnalysisEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "contribution_ids": ["large_cap"],
                "contribution_definitions": definitions,
                "metrics": ["total_pnl"],
            },
            contributions=[
                {
                    "contribution_id": "large_cap",
                    "contribution_definition": {
                        "dimension": "market_cap_bucket",
                        "bucket": "mid",
                    },
                    "source_research_run_id": source_run_id,
                }
            ],
        )
    assert repository.persisted == []


def test_contribution_producer_rejects_identity_or_order_drift():
    repository = _Repository()
    definitions = {
        "large_cap": {"dimension": "market_cap_bucket", "bucket": "large"},
        "mid_cap": {"dimension": "market_cap_bucket", "bucket": "mid"},
    }
    first_run, second_run = uuid4(), uuid4()
    resolver = _SourceResolver({
        first_run: _certified(),
        second_run: _certified(),
    })
    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_SOURCE_IDS_MISMATCH",
    ):
        ContributionAnalysisEvidenceProducer(repository, resolver).produce(
            uuid4(),
            stage_protocol={
                "contribution_ids": ["large_cap", "mid_cap"],
                "contribution_definitions": definitions,
                "metrics": ["total_pnl"],
            },
            contributions=[
                {
                    "contribution_id": "mid_cap",
                    "contribution_definition": definitions["mid_cap"],
                    "source_research_run_id": first_run,
                },
                {
                    "contribution_id": "large_cap",
                    "contribution_definition": definitions["large_cap"],
                    "source_research_run_id": second_run,
                },
            ],
        )
    assert repository.persisted == []
