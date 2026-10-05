from __future__ import annotations

from dataclasses import dataclass

from hope.application.experiments.strategy_family_registry import STRATEGY_FAMILY_REGISTRY
from hope.domain.strategy_family import ResearchMarket, ResearchRegime, StrategyFamilyResearchContract


class StrategyFamilyResearchEligibilityError(ValueError):
    """Raised when a research run is not authorized by the canonical family contract."""


@dataclass(frozen=True, slots=True)
class StrategyFamilyResearchBinding:
    family: str
    market: ResearchMarket
    regime: ResearchRegime
    research_only: bool = True


def _contract_for_family(family: str) -> StrategyFamilyResearchContract:
    matches = tuple(contract for contract in STRATEGY_FAMILY_REGISTRY if contract.family == family)
    if len(matches) != 1:
        raise StrategyFamilyResearchEligibilityError(
            f"RESEARCH_STRATEGY_FAMILY_NOT_CANONICAL:{family}"
        )
    return matches[0]


def bind_strategy_family_research(
    *,
    family: str,
    market: ResearchMarket,
    regime: ResearchRegime,
) -> StrategyFamilyResearchBinding:
    """Bind a research run to canonical family/market/regime eligibility.

    This is intentionally a research boundary only. It cannot grant PAPER or LIVE
    permission and fails closed for unknown or ineligible combinations.
    """
    if not isinstance(market, ResearchMarket):
        raise StrategyFamilyResearchEligibilityError("RESEARCH_MARKET_NOT_CANONICAL")
    if not isinstance(regime, ResearchRegime):
        raise StrategyFamilyResearchEligibilityError("RESEARCH_REGIME_NOT_CANONICAL")

    contract = _contract_for_family(family)
    if contract.research_only is not True:
        raise StrategyFamilyResearchEligibilityError("RESEARCH_STRATEGY_FAMILY_NOT_RESEARCH_ONLY")
    if not contract.eligible_for_research(market=market, regime=regime):
        raise StrategyFamilyResearchEligibilityError(
            f"RESEARCH_STRATEGY_FAMILY_INELIGIBLE:{family}:{market.value}:{regime.value}"
        )

    return StrategyFamilyResearchBinding(
        family=contract.family,
        market=market,
        regime=regime,
    )
