from __future__ import annotations

from hope.domain.strategy_family import (
    ResearchDirection,
    ResearchMarket,
    ResearchRegime,
    StrategyFamilyResearchContract,
)

I = ResearchMarket.INDIA
U = ResearchMarket.USA
BULL = ResearchRegime.BULL_TREND
BEAR = ResearchRegime.BEAR_TREND
SIDEWAYS = ResearchRegime.SIDEWAYS
RECOVERY = ResearchRegime.RECOVERY
LONG = ResearchDirection.LONG
DEFENSIVE = ResearchDirection.DEFENSIVE


STRATEGY_FAMILY_REGISTRY: tuple[StrategyFamilyResearchContract, ...] = (
    StrategyFamilyResearchContract("CROSS_SECTIONAL_MOMENTUM", frozenset({I, U}), frozenset({BULL, RECOVERY}), frozenset({LONG})),
    StrategyFamilyResearchContract("FIFTY_TWO_WEEK_HIGH", frozenset({I, U}), frozenset({BULL, RECOVERY}), frozenset({LONG})),
    StrategyFamilyResearchContract("TIME_SERIES_TREND", frozenset({I, U}), frozenset({BULL, BEAR}), frozenset({LONG, DEFENSIVE})),
    StrategyFamilyResearchContract("LOW_VOLATILITY_DEFENSIVE", frozenset({I, U}), frozenset({BEAR, SIDEWAYS}), frozenset({DEFENSIVE})),
    StrategyFamilyResearchContract("QUALITY_DEFENSIVE", frozenset({I, U}), frozenset({BEAR, SIDEWAYS, RECOVERY}), frozenset({DEFENSIVE})),
    StrategyFamilyResearchContract("MULTIFACTOR_QMV", frozenset({I, U}), frozenset({BEAR, SIDEWAYS}), frozenset({DEFENSIVE})),
    StrategyFamilyResearchContract("SECTOR_INDUSTRY_MOMENTUM", frozenset({I, U}), frozenset({BULL}), frozenset({LONG})),
    StrategyFamilyResearchContract("SHORT_TERM_REVERSAL", frozenset({I, U}), frozenset({SIDEWAYS, RECOVERY}), frozenset({LONG})),
    StrategyFamilyResearchContract("EARNINGS_DRIFT", frozenset({U}), frozenset({BULL, SIDEWAYS, RECOVERY}), frozenset({LONG})),
    StrategyFamilyResearchContract("VALUE_RECOVERY", frozenset({I, U}), frozenset({SIDEWAYS, RECOVERY}), frozenset({LONG})),
)

EXPECTED_STRATEGY_FAMILIES = frozenset(
    {
        "CROSS_SECTIONAL_MOMENTUM",
        "FIFTY_TWO_WEEK_HIGH",
        "TIME_SERIES_TREND",
        "LOW_VOLATILITY_DEFENSIVE",
        "QUALITY_DEFENSIVE",
        "MULTIFACTOR_QMV",
        "SECTOR_INDUSTRY_MOMENTUM",
        "SHORT_TERM_REVERSAL",
        "EARNINGS_DRIFT",
        "VALUE_RECOVERY",
    }
)


def validate_strategy_family_registry(
    registry: tuple[StrategyFamilyResearchContract, ...] = STRATEGY_FAMILY_REGISTRY,
) -> None:
    names = tuple(contract.family for contract in registry)
    if len(names) != len(set(names)):
        raise ValueError("strategy family registry contains duplicate family identities")
    if frozenset(names) != EXPECTED_STRATEGY_FAMILIES:
        missing = sorted(EXPECTED_STRATEGY_FAMILIES - frozenset(names))
        foreign = sorted(frozenset(names) - EXPECTED_STRATEGY_FAMILIES)
        raise ValueError(f"strategy family registry identity mismatch: missing={missing}, foreign={foreign}")
    if any(contract.research_only is not True for contract in registry):
        raise ValueError("strategy family registry may contain RESEARCH_ONLY contracts only")


validate_strategy_family_registry()
