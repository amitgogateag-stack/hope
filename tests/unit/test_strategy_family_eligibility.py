import pytest

from hope.application.experiments.strategy_family_eligibility import (
    StrategyFamilyResearchEligibilityError,
    bind_strategy_family_research,
)
from hope.domain.strategy_family import ResearchMarket, ResearchRegime


def test_canonical_eligible_family_binds_research_context() -> None:
    binding = bind_strategy_family_research(
        family="CROSS_SECTIONAL_MOMENTUM",
        market=ResearchMarket.USA,
        regime=ResearchRegime.BULL_TREND,
    )
    assert binding.family == "CROSS_SECTIONAL_MOMENTUM"
    assert binding.market is ResearchMarket.USA
    assert binding.regime is ResearchRegime.BULL_TREND
    assert binding.research_only is True
    assert not hasattr(binding, "paper_enabled")
    assert not hasattr(binding, "live_enabled")


def test_unknown_family_fails_closed() -> None:
    with pytest.raises(StrategyFamilyResearchEligibilityError, match="NOT_CANONICAL"):
        bind_strategy_family_research(
            family="UNKNOWN_ALPHA",
            market=ResearchMarket.USA,
            regime=ResearchRegime.BULL_TREND,
        )


def test_market_ineligible_family_fails_closed() -> None:
    with pytest.raises(StrategyFamilyResearchEligibilityError, match="INELIGIBLE"):
        bind_strategy_family_research(
            family="EARNINGS_DRIFT",
            market=ResearchMarket.INDIA,
            regime=ResearchRegime.BULL_TREND,
        )


def test_regime_ineligible_family_fails_closed() -> None:
    with pytest.raises(StrategyFamilyResearchEligibilityError, match="INELIGIBLE"):
        bind_strategy_family_research(
            family="SECTOR_INDUSTRY_MOMENTUM",
            market=ResearchMarket.USA,
            regime=ResearchRegime.SIDEWAYS,
        )


def test_raw_market_string_is_rejected_instead_of_coerced() -> None:
    with pytest.raises(StrategyFamilyResearchEligibilityError, match="MARKET_NOT_CANONICAL"):
        bind_strategy_family_research(
            family="CROSS_SECTIONAL_MOMENTUM",
            market="USA",  # type: ignore[arg-type]
            regime=ResearchRegime.BULL_TREND,
        )


def test_raw_regime_string_is_rejected_instead_of_coerced() -> None:
    with pytest.raises(StrategyFamilyResearchEligibilityError, match="REGIME_NOT_CANONICAL"):
        bind_strategy_family_research(
            family="CROSS_SECTIONAL_MOMENTUM",
            market=ResearchMarket.USA,
            regime="BULL_TREND",  # type: ignore[arg-type]
        )
