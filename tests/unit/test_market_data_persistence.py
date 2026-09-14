from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.domain.market_data.models import MarketBar
from hope.infrastructure.market_data.ingestion import (
    IngestionRejection,
    IngestionRejectionReason,
    NormalizedMarketBarBatch,
)
from hope.infrastructure.market_data.persistence import persist_normalized_batch


NOW = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)


def market_bar() -> MarketBar:
    return MarketBar(
        instrument_id=str(uuid4()),
        event_time=NOW,
        available_time=NOW,
        effective_time=None,
        ingestion_time=NOW,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[MarketBar, ...]]] = []

    def append(self, dataset_version_id, bars) -> None:
        self.calls.append((dataset_version_id, bars))


def test_persists_only_complete_normalized_batch() -> None:
    sink = RecordingSink()
    dataset_version_id = uuid4()
    bar = market_bar()
    batch = NormalizedMarketBarBatch(
        bars=(bar,),
        rejections=(),
        input_count=1,
    )

    persist_normalized_batch(sink, dataset_version_id, batch)

    assert sink.calls == [(dataset_version_id, (bar,))]


def test_rejects_partial_batch_without_touching_sink() -> None:
    sink = RecordingSink()
    batch = NormalizedMarketBarBatch(
        bars=(market_bar(),),
        rejections=(
            IngestionRejection(
                index=1,
                source="TEST",
                source_symbol="BAD",
                event_time=NOW,
                reason=IngestionRejectionReason.UNRESOLVED_INSTRUMENT,
            ),
        ),
        input_count=2,
    )

    with pytest.raises(ValueError, match="NOT_SAFE_TO_PERSIST"):
        persist_normalized_batch(sink, uuid4(), batch)

    assert sink.calls == []


def test_rejects_cardinality_mismatch_without_touching_sink() -> None:
    sink = RecordingSink()
    batch = NormalizedMarketBarBatch(
        bars=(market_bar(),),
        rejections=(),
        input_count=2,
    )

    with pytest.raises(ValueError, match="NOT_SAFE_TO_PERSIST"):
        persist_normalized_batch(sink, uuid4(), batch)

    assert sink.calls == []


def test_rejects_duplicate_logical_bars_without_touching_sink() -> None:
    sink = RecordingSink()
    duplicate = market_bar()
    batch = NormalizedMarketBarBatch(
        bars=(duplicate, duplicate),
        rejections=(),
        input_count=2,
    )

    with pytest.raises(ValueError, match="NOT_SAFE_TO_PERSIST"):
        persist_normalized_batch(sink, uuid4(), batch)

    assert sink.calls == []


def test_rejects_empty_batch_without_touching_sink() -> None:
    sink = RecordingSink()
    batch = NormalizedMarketBarBatch(bars=(), rejections=(), input_count=0)

    with pytest.raises(ValueError, match="NOT_SAFE_TO_PERSIST"):
        persist_normalized_batch(sink, uuid4(), batch)

    assert sink.calls == []


def test_requires_uuid_dataset_version_identity() -> None:
    sink = RecordingSink()
    batch = NormalizedMarketBarBatch(
        bars=(market_bar(),),
        rejections=(),
        input_count=1,
    )

    with pytest.raises(TypeError, match="REQUIRES_DATASET_VERSION_ID"):
        persist_normalized_batch(sink, "not-a-uuid", batch)  # type: ignore[arg-type]

    assert sink.calls == []
