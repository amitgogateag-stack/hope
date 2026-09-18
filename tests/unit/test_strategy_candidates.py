from uuid import uuid4

import pytest

from hope.domain.strategy.candidates import (
    StrategyCandidateClassification,
    StrategyCandidateState,
    StrategyMarket,
)


def test_strategy_candidate_requires_market_scope() -> None:
    with pytest.raises(ValueError, match="STRATEGY_CANDIDATE_MARKET_SCOPE_REQUIRED"):
        StrategyCandidateClassification(
            strategy_version_id=uuid4(),
            markets=frozenset(),
            state=StrategyCandidateState.RESEARCH,
            rationale="Initial research registration",
        )


def test_nonresearch_candidate_requires_research_decision() -> None:
    with pytest.raises(ValueError, match="STRATEGY_CANDIDATE_RESEARCH_DECISION_REQUIRED"):
        StrategyCandidateClassification(
            strategy_version_id=uuid4(),
            markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
            state=StrategyCandidateState.BACKUP_CANDIDATE,
            rationale="Evidence-backed backup classification",
        )


def test_research_candidate_supports_india_and_us_scope() -> None:
    definition = StrategyCandidateClassification(
        strategy_version_id=uuid4(),
        markets=frozenset({StrategyMarket.USA, StrategyMarket.INDIA}),
        state=StrategyCandidateState.RESEARCH,
        rationale="Cross-market research candidate",
    )
    assert definition.markets == frozenset({StrategyMarket.INDIA, StrategyMarket.USA})
