from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable
from uuid import UUID

from hope.infrastructure.market_data.ingestion import NormalizedMarketBarBatch
from hope.infrastructure.market_data.persistence import MarketBarSink
from hope.infrastructure.market_data.pipeline import ingest_market_data_window
from hope.infrastructure.market_data.provider import MarketDataProvider, MarketDataRequest


@runtime_checkable
class MarketDataRecoveryAuthorizer(Protocol):
    def preflight_subset(
        self,
        dataset_version_id: UUID,
        requests: tuple[MarketDataRequest, ...],
        *,
        identity_map: Mapping[tuple[str, str], UUID],
    ) -> None:
        ...


def ingest_market_data_recovery_windows(
    provider: MarketDataProvider,
    sink: MarketBarSink,
    authorizer: MarketDataRecoveryAuthorizer,
    dataset_version_id: UUID,
    requests: tuple[MarketDataRequest, ...],
    *,
    identity_map: Mapping[tuple[str, str], UUID],
) -> tuple[NormalizedMarketBarBatch, ...]:
    """Persist an authorized subset of a durable manifest without sealing it.

    This is the restart/recovery path. It deliberately does not finalize a
    dataset version: retries may refill any declared subset, while the existing
    full lifecycle remains the only path that proves complete coverage and seals.
    The sink owns durable duplicate-idempotency semantics.
    """
    if not isinstance(dataset_version_id, UUID):
        raise TypeError("MARKET_DATA_RECOVERY_REQUIRES_DATASET_VERSION_ID")
    if not isinstance(requests, tuple):
        raise TypeError("MARKET_DATA_RECOVERY_WINDOWS_REQUIRE_TUPLE")
    if not requests:
        raise ValueError("MARKET_DATA_RECOVERY_WINDOWS_REQUIRED")

    required_identity_keys = {
        (request.source, symbol)
        for request in requests
        for symbol in request.source_symbols
    }
    if any(key not in identity_map for key in required_identity_keys):
        raise ValueError("MARKET_DATA_RECOVERY_IDENTITY_MAP_INCOMPLETE")
    if any(not isinstance(identity_map[key], UUID) for key in required_identity_keys):
        raise TypeError("MARKET_DATA_RECOVERY_IDENTITY_MAP_INVALID")

    preflight_subset = getattr(authorizer, "preflight_subset", None)
    if not callable(preflight_subset):
        raise TypeError("MARKET_DATA_RECOVERY_MANIFEST_PREFLIGHT_REQUIRED")
    preflight_subset(dataset_version_id, requests, identity_map=identity_map)

    return tuple(
        ingest_market_data_window(
            provider,
            sink,
            dataset_version_id,
            request,
            identity_map=identity_map,
        )
        for request in requests
    )
