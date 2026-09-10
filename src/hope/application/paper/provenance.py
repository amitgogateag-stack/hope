from __future__ import annotations

from uuid import UUID

from hope.application.experiments.config_hash import configuration_hash
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.market_data.context import PITMarketContext
from hope.domain.strategy.models import ParameterSnapshot, Strategy


def paper_decision_inputs_hash(
    strategy: Strategy,
    market_context: PITMarketContext,
    universe: UniverseSnapshot,
    parameters: ParameterSnapshot,
    *,
    dataset_version_id: UUID | None = None,
) -> str:
    """Hash the exact PAPER decision inputs, including durable universe and dataset identity."""
    return configuration_hash(
        {
            "strategy": {
                "name": strategy.name,
                "version": strategy.version,
            },
            "market_context": {
                "dataset_version_id": (
                    str(dataset_version_id) if dataset_version_id is not None else None
                ),
                "context": market_context.model_dump(mode="json"),
            },
            "universe": {
                "universe_version_id": str(universe.universe_version_id),
                "version": universe.version.model_dump(mode="json"),
                "membership_hash": universe.membership_hash,
            },
            "parameters": parameters.model_dump(mode="json"),
        }
    )
