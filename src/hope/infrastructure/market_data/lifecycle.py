from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable
from uuid import UUID

from hope.infrastructure.market_data.ingestion import NormalizedMarketBarBatch
from hope.infrastructure.market_data.persistence import MarketBarSink
from hope.infrastructure.market_data.pipeline import ingest_market_data_window
from hope.infrastructure.market_data.provider import MarketDataProvider, MarketDataRequest


@runtime_checkable
class MarketDataVersionSealer(Protocol):
    def seal(self, dataset_version_id: UUID) -> None:
        ...


def ingest_and_seal_market_data_windows(
    provider: MarketDataProvider,
    sink: MarketBarSink,
    sealer: MarketDataVersionSealer,
    dataset_version_id: UUID,
    requests: tuple[MarketDataRequest, ...],
    *,
    identity_map: Mapping[tuple[str, str], UUID],
) -> tuple[NormalizedMarketBarBatch, ...]:
    """Ingest a deterministic contiguous request set, then seal exactly once.

    Lifecycle preflight is completed before the provider is called so malformed
    request sets or identity maps cannot leave a partially written dataset.
    If a later provider/persistence step fails, the version remains staging and
    is never sealed; exact retries rely on the append sink's idempotency.
    """

    if not isinstance(dataset_version_id, UUID):
        raise TypeError("MARKET_DATA_LIFECYCLE_REQUIRES_DATASET_VERSION_ID")
    if not requests:
        raise ValueError("MARKET_DATA_LIFECYCLE_WINDOWS_REQUIRED")

    first = requests[0]
    for request in requests:
        if (
            request.source != first.source
            or request.source_symbols != first.source_symbols
            or request.interval != first.interval
        ):
            raise ValueError("MARKET_DATA_LIFECYCLE_WINDOWS_NOT_HOMOGENEOUS")

    for previous, current in zip(requests, requests[1:]):
        if current.start != previous.end:
            raise ValueError("MARKET_DATA_LIFECYCLE_WINDOWS_NOT_CONTIGUOUS")

    required_identity_keys = {
        (request.source, symbol)
        for request in requests
        for symbol in request.source_symbols
    }
    if any(key not in identity_map for key in required_identity_keys):
        raise ValueError("MARKET_DATA_LIFECYCLE_IDENTITY_MAP_INCOMPLETE")
    if any(not isinstance(identity_map[key], UUID) for key in required_identity_keys):
        raise TypeError("MARKET_DATA_LIFECYCLE_IDENTITY_MAP_INVALID")

    batches = tuple(
        ingest_market_data_window(
            provider,
            sink,
            dataset_version_id,
            request,
            identity_map=identity_map,
        )
        for request in requests
    )
    sealer.seal(dataset_version_id)
    return batches
