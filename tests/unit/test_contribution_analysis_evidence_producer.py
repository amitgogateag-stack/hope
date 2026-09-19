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


def test_contribution_producer_requires_predeclared_bucket_definitions():
    repository = _Repository()
    definitions = {
        "large_cap": {"dimension": "market_cap_bucket", "bucket": "large"},
        "mid_cap": {"dimension": "market_cap_bucket", "bucket": "mid"},
    }
    record = ContributionAnalysisEvidenceProducer(repository).produce(
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
                "certified_result": _certified("12", "1.1"),
            },
            {
                "contribution_id": "mid_cap",
                "contribution_definition": definitions["mid_cap"],
                "certified_result": _certified("8", "0.7"),
            },
        ],
    )

    assert repository.persisted == [record]
    artifact = record.canonical_result
    assert artifact["schema"] == "hope.contribution-analysis-evidence.v1"
    assert artifact["contributions"][0]["contribution_definition"] == definitions["large_cap"]
    assert artifact["result_fingerprint"] == configuration_hash(
        artifact["contributions"]
    )
    assert "winner" not in artifact
    assert "best" not in artifact


def test_contribution_producer_rejects_retrospective_rank_definition():
    repository = _Repository()
    definitions = {
        "top_decile": {"dimension": "symbol_rank", "bucket": "top_decile"},
    }
    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_RETROSPECTIVE_DEFINITION_FORBIDDEN:top_decile",
    ):
        ContributionAnalysisEvidenceProducer(repository).produce(
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
    definitions = {
        "large_cap": {"dimension": "market_cap_bucket", "bucket": "large"},
    }
    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_PREDECLARED_DEFINITION_MISMATCH:large_cap",
    ):
        ContributionAnalysisEvidenceProducer(repository).produce(
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
                    "certified_result": _certified(),
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
    with pytest.raises(
        ValueError,
        match="CONTRIBUTION_ANALYSIS_SOURCE_IDS_MISMATCH",
    ):
        ContributionAnalysisEvidenceProducer(repository).produce(
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
                    "certified_result": _certified(),
                },
                {
                    "contribution_id": "large_cap",
                    "contribution_definition": definitions["large_cap"],
                    "certified_result": _certified(),
                },
            ],
        )
    assert repository.persisted == []
