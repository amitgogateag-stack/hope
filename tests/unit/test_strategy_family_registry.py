import pytest

from hope.application.experiments.strategy_family_registry import (
    EXPECTED_STRATEGY_FAMILIES,
    STRATEGY_FAMILY_REGISTRY,
    validate_strategy_family_registry,
)
from hope.domain.strategy_family import (
    ResearchDirection,
    ResearchMarket,
    ResearchRegime,
    StrategyFamilyResearchContract,
)


def test_registry_is_complete_unique_and_research_only() -> None:
    validate_strategy_family_registry()
    assert len(STRATEGY_FAMILY_REGISTRY) == 10
    assert {item.family for item in STRATEGY_FAMILY_REGISTRY} == EXPECTED_STRATEGY_FAMILIES
    assert all(item.research_only is True for item in STRATEGY_FAMILY_REGISTRY)


def test_earnings_drift_is_us_first() -> None:
    contract = next(item for item in STRATEGY_FAMILY_REGISTRY if item.family == "EARNINGS_DRIFT")
    assert contract.markets == frozenset({ResearchMarket.USA})
    assert not contract.eligible_for_research(market=ResearchMarket.INDIA, regime=ResearchRegime.BULL_TREND)


def test_regime_metadata_is_evaluation_eligibility_not_trading_permission() -> None:
    contract = next(item for item in STRATEGY_FAMILY_REGISTRY if item.family == "CROSS_SECTIONAL_MOMENTUM")
    assert contract.eligible_for_research(market=ResearchMarket.USA, regime=ResearchRegime.BULL_TREND)
    assert contract.research_only is True
    assert not hasattr(contract, "live_enabled")
    assert not hasattr(contract, "paper_enabled")


def test_registry_rejects_missing_family() -> None:
    with pytest.raises(ValueError, match="identity mismatch"):
        validate_strategy_family_registry(STRATEGY_FAMILY_REGISTRY[:-1])


def test_registry_rejects_duplicate_family() -> None:
    duplicate = STRATEGY_FAMILY_REGISTRY + (STRATEGY_FAMILY_REGISTRY[0],)
    with pytest.raises(ValueError, match="duplicate"):
        validate_strategy_family_registry(duplicate)


def test_contract_rejects_non_research_only_state() -> None:
    with pytest.raises(ValueError, match="RESEARCH_ONLY"):
        StrategyFamilyResearchContract(
            family="TEST_FAMILY",
            markets=frozenset({ResearchMarket.USA}),
            regimes=frozenset({ResearchRegime.BULL_TREND}),
            directions=frozenset({ResearchDirection.LONG}),
            research_only=False,
        )


def test_short_research_fails_closed_without_borrow_model() -> None:
    with pytest.raises(ValueError, match="borrow"):
        StrategyFamilyResearchContract(
            family="TEST_SHORT",
            markets=frozenset({ResearchMarket.INDIA}),
            regimes=frozenset({ResearchRegime.BEAR_TREND}),
            directions=frozenset({ResearchDirection.SHORT_RESEARCH}),
        )


def test_short_research_requires_explicit_borrow_model() -> None:
    contract = StrategyFamilyResearchContract(
        family="TEST_SHORT",
        markets=frozenset({ResearchMarket.USA}),
        regimes=frozenset({ResearchRegime.BEAR_TREND}),
        directions=frozenset({ResearchDirection.SHORT_RESEARCH}),
        requires_borrow_model=True,
    )
    assert contract.research_only is True
    assert contract.requires_borrow_model is True
