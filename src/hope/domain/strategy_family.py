from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResearchMarket(StrEnum):
    INDIA = "INDIA"
    USA = "USA"


class ResearchRegime(StrEnum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    SIDEWAYS = "SIDEWAYS"
    RECOVERY = "RECOVERY"


class ResearchDirection(StrEnum):
    LONG = "LONG"
    DEFENSIVE = "DEFENSIVE"
    SHORT_RESEARCH = "SHORT_RESEARCH"


@dataclass(frozen=True, slots=True)
class StrategyFamilyResearchContract:
    family: str
    markets: frozenset[ResearchMarket]
    regimes: frozenset[ResearchRegime]
    directions: frozenset[ResearchDirection]
    research_only: bool = True
    requires_borrow_model: bool = False

    def __post_init__(self) -> None:
        if not self.family or self.family != self.family.strip() or self.family.upper() != self.family:
            raise ValueError("strategy family must be a canonical non-empty uppercase identifier")
        if not self.markets:
            raise ValueError("strategy family must declare at least one research market")
        if not self.regimes:
            raise ValueError("strategy family must declare at least one evaluation regime")
        if not self.directions:
            raise ValueError("strategy family must declare at least one research direction")
        if self.research_only is not True:
            raise ValueError("strategy family contracts are RESEARCH_ONLY")
        if ResearchDirection.SHORT_RESEARCH in self.directions and not self.requires_borrow_model:
            raise ValueError("short research requires an explicit borrow/implementation model")

    def eligible_for_research(self, *, market: ResearchMarket, regime: ResearchRegime) -> bool:
        """Return research eligibility only; this never grants PAPER or LIVE permission."""
        return market in self.markets and regime in self.regimes
