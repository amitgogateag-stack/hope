from __future__ import annotations

from datetime import timedelta
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
    market_data_max_age: timedelta | None = None,
) -> str:
    """Hash the exact PAPER decision inputs, including durable universe and dataset identity."""
    if market_data_max_age is not None:
        if not isinstance(market_data_max_age, timedelta):
            raise TypeError("PAPER_DECISION_INPUTS_REQUIRES_FRESHNESS_TIMEDELTA")
        if market_data_max_age <= timedelta(0):
            raise ValueError("PAPER_DECISION_INPUTS_REQUIRES_POSITIVE_FRESHNESS")
        freshness_microseconds = (
            market_data_max_age.days * 86_400_000_000
            + market_data_max_age.seconds * 1_000_000
            + market_data_max_age.microseconds
        )
    else:
        freshness_microseconds = None
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
            "market_data_freshness": {
                "max_age_microseconds": freshness_microseconds,
            },
        }
    )
