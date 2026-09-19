from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from hope.domain.strategy.candidates import StrategyMarket


class MarketRegime(StrEnum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    SIDEWAYS = "SIDEWAYS"
    RECOVERY = "RECOVERY"


class StrategyFamilyPriority(StrEnum):
    CORE = "CORE"
    SECONDARY = "SECONDARY"


class StrategyFamilyDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    name: str
    markets: frozenset[StrategyMarket]
    regimes: frozenset[MarketRegime]
    priority: StrategyFamilyPriority
    implementation_note: str


STRATEGY_FAMILY_CATALOG: tuple[StrategyFamilyDefinition, ...] = (
    StrategyFamilyDefinition(
        code="CROSS_SECTIONAL_MOMENTUM",
        name="Cross-sectional momentum",
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.BULL_TREND, MarketRegime.RECOVERY}),
        priority=StrategyFamilyPriority.CORE,
        implementation_note="Rank liquid stocks by medium-horizon relative strength; research long-only first.",
    ),
    StrategyFamilyDefinition(
        code="FIFTY_TWO_WEEK_HIGH",
        name="52-week-high momentum",
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.BULL_TREND, MarketRegime.RECOVERY}),
        priority=StrategyFamilyPriority.CORE,
        implementation_note="Rank proximity to trailing 52-week high with liquidity and concentration controls.",
    ),
    StrategyFamilyDefinition(
        code="TIME_SERIES_TREND",
        name="Time-series trend",
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.BULL_TREND, MarketRegime.BEAR_TREND}),
        priority=StrategyFamilyPriority.CORE,
        implementation_note="Trend family includes moving-average and breakout variants; long-flat first where short implementation is constrained.",
    ),
    StrategyFamilyDefinition(
        code="LOW_VOLATILITY_DEFENSIVE",
        name="Low-volatility defensive",
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.BEAR_TREND, MarketRegime.SIDEWAYS}),
        priority=StrategyFamilyPriority.CORE,
        implementation_note="Prefer liquid lower-volatility equities; treat as defensive allocation, not a market-timing guarantee.",
    ),
    StrategyFamilyDefinition(
        code="QUALITY_DEFENSIVE",
        name="Quality defensive",
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.BEAR_TREND, MarketRegime.SIDEWAYS, MarketRegime.RECOVERY}),
        priority=StrategyFamilyPriority.CORE,
        implementation_note="Use profitability, leverage and earnings-stability signals with point-in-time fundamentals.",
    ),
    StrategyFamilyDefinition(
        code="MULTIFACTOR_QMV",
        name="Quality-momentum-low-volatility multifactor",
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.BULL_TREND, MarketRegime.BEAR_TREND, MarketRegime.SIDEWAYS, MarketRegime.RECOVERY}),
        priority=StrategyFamilyPriority.CORE,
        implementation_note="Combine independently validated quality, momentum and low-volatility signals to reduce single-factor cyclicality.",
    ),
    StrategyFamilyDefinition(
        code="SECTOR_INDUSTRY_MOMENTUM",
        name="Sector and industry momentum",
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.BULL_TREND, MarketRegime.RECOVERY}),
        priority=StrategyFamilyPriority.SECONDARY,
        implementation_note="Rank sectors or industries before stock selection; requires PIT industry classification.",
    ),
    StrategyFamilyDefinition(
        code="SHORT_TERM_REVERSAL",
        name="Short-term reversal",
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.SIDEWAYS, MarketRegime.RECOVERY}),
        priority=StrategyFamilyPriority.SECONDARY,
        implementation_note="Restrict to liquidity-aware, non-panic conditions; explicitly stress spread, impact and turnover.",
    ),
    StrategyFamilyDefinition(
        code="EARNINGS_DRIFT",
        name="Post-earnings announcement drift",
        markets=frozenset({StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.BULL_TREND, MarketRegime.BEAR_TREND, MarketRegime.SIDEWAYS, MarketRegime.RECOVERY}),
        priority=StrategyFamilyPriority.SECONDARY,
        implementation_note="USA-first event strategy requiring PIT earnings surprise and announcement timestamps.",
    ),
    StrategyFamilyDefinition(
        code="VALUE_RECOVERY",
        name="Value and recovery",
        markets=frozenset({StrategyMarket.INDIA, StrategyMarket.USA}),
        regimes=frozenset({MarketRegime.RECOVERY, MarketRegime.SIDEWAYS}),
        priority=StrategyFamilyPriority.SECONDARY,
        implementation_note="Long-horizon research family; pair valuation with quality controls to reduce value-trap exposure.",
    ),
)
