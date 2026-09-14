from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from hope.domain.market_data.models import MarketBar
from hope.infrastructure.market_data.ingestion import NormalizedMarketBarBatch


@runtime_checkable
class MarketBarSink(Protocol):
    """Append-only persistence boundary for one dataset version."""

    def append(
        self,
        dataset_version_id: UUID,
        bars: tuple[MarketBar, ...],
    ) -> None:
        ...


def persist_normalized_batch(
    sink: MarketBarSink,
    dataset_version_id: UUID,
    batch: NormalizedMarketBarBatch,
) -> None:
    """Persist only a fully accepted, non-empty normalized batch.

    The normalization boundary is authoritative: partial batches and empty
    provider responses are never persisted as if they were valid evidence.
    """

    if not isinstance(dataset_version_id, UUID):
        raise TypeError("MARKET_DATA_PERSIST_REQUIRES_DATASET_VERSION_ID")
    if not batch.safe_to_persist:
        raise ValueError("MARKET_DATA_BATCH_NOT_SAFE_TO_PERSIST")
    sink.append(dataset_version_id, batch.bars)
