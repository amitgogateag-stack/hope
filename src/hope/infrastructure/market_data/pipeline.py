from __future__ import annotations

from typing import Mapping
from uuid import UUID

from hope.infrastructure.market_data.ingestion import NormalizedMarketBarBatch
from hope.infrastructure.market_data.persistence import MarketBarSink, persist_normalized_batch
from hope.infrastructure.market_data.provider import (
    MarketDataProvider,
    MarketDataRequest,
    fetch_and_normalize_bars,
)


def ingest_market_data_window(
    provider: MarketDataProvider,
    sink: MarketBarSink,
    dataset_version_id: UUID,
    request: MarketDataRequest,
    *,
    identity_map: Mapping[tuple[str, str], UUID],
) -> NormalizedMarketBarBatch:
    """Authoritative provider -> normalize -> persist path for one closed window.

    Dataset identity is validated before any provider side effect. Provider
    output is normalized through the fail-closed boundary and is persisted only
    when the normalized batch is fully safe.
    """

    if not isinstance(dataset_version_id, UUID):
        raise TypeError("MARKET_DATA_PIPELINE_REQUIRES_DATASET_VERSION_ID")

    batch = fetch_and_normalize_bars(
        provider,
        request,
        identity_map=identity_map,
    )
    persist_normalized_batch(sink, dataset_version_id, batch)
    return batch
