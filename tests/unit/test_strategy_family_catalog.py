from hope.domain.strategy.families import (
    MarketRegime,
    STRATEGY_FAMILY_CATALOG,
    StrategyFamilyPriority,
)
from hope.domain.strategy.candidates import StrategyMarket


def test_strategy_family_catalog_has_ten_distinct_research_families() -> None:
    assert len(STRATEGY_FAMILY_CATALOG) == 10
    assert len({family.code for family in STRATEGY_FAMILY_CATALOG}) == 10


def test_strategy_family_catalog_spans_india_usa_bull_and_bear() -> None:
    assert any(StrategyMarket.INDIA in family.markets for family in STRATEGY_FAMILY_CATALOG)
    assert any(StrategyMarket.USA in family.markets for family in STRATEGY_FAMILY_CATALOG)
    assert any(MarketRegime.BULL_TREND in family.regimes for family in STRATEGY_FAMILY_CATALOG)
    assert any(MarketRegime.BEAR_TREND in family.regimes for family in STRATEGY_FAMILY_CATALOG)


def test_core_catalog_contains_offensive_and_defensive_families() -> None:
    core_codes = {
        family.code
        for family in STRATEGY_FAMILY_CATALOG
        if family.priority is StrategyFamilyPriority.CORE
    }
    assert "CROSS_SECTIONAL_MOMENTUM" in core_codes
    assert "TIME_SERIES_TREND" in core_codes
    assert "LOW_VOLATILITY_DEFENSIVE" in core_codes
    assert "QUALITY_DEFENSIVE" in core_codes
    assert "MULTIFACTOR_QMV" in core_codes
