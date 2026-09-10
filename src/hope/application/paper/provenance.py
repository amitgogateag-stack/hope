from __future__ import annotations

from hope.application.experiments.config_hash import configuration_hash
from hope.domain.market_data.context import PITMarketContext
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.domain.universe.models import UniverseVersion


def paper_decision_inputs_hash(
    strategy: Strategy,
    market_context: PITMarketContext,
    universe: UniverseVersion,
    parameters: ParameterSnapshot,
) -> str:
    """Hash the exact PAPER decision inputs represented by current domain models."""
    return configuration_hash(
        {
            "strategy": {
                "name": strategy.name,
                "version": strategy.version,
            },
            "market_context": market_context.model_dump(mode="json"),
            "universe": universe.model_dump(mode="json"),
            "parameters": parameters.model_dump(mode="json"),
        }
    )
