from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from hope.domain.market_data.models import MarketBar
from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.pipeline import ingest_market_data_window
from hope.infrastructure.market_data.provider import MarketDataRequest, ProviderMarketDataBatch


class StubProvider:
    source = "TEST"

    def __init__(self, batch: ProviderMarketDataBatch) -> None:
        self.batch = batch
        self.calls: list[MarketDataRequest] = []

    def fetch_bars(self, request: MarketDataRequest) -> ProviderMarketDataBatch:
        self.calls.append(request)
        return self.batch


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, tuple[MarketBar, ...]]] = []

    def append(self, dataset_version_id: UUID, bars: tuple[MarketBar, ...]) -> None:
        self.calls.append((dataset_version_id, bars))


def test_pipeline_fetches_normalizes_and_persists_one_safe_batch() -> None:
    request, batch, instrument_id = _request_and_batch()
    provider = StubProvider(batch)
    sink = RecordingSink()
    dataset_version_id = uuid4()

    normalized = ingest_market_data_window(
        provider,
        sink,
        dataset_version_id,
        request,
        identity_map={("TEST", "ABC"): instrument_id},
    )

    assert provider.calls == [request]
    assert normalized.safe_to_persist is True
    assert len(normalized.bars) == 1
    assert normalized.bars[0].instrument_id == str(instrument_id)
    assert sink.calls == [(dataset_version_id, normalized.bars)]


def test_pipeline_rejects_unsafe_normalization_without_touching_sink() -> None:
    request, batch, _ = _request_and_batch()
    provider = StubProvider(batch)
    sink = RecordingSink()

    with pytest.raises(ValueError, match="MARKET_DATA_BATCH_NOT_SAFE_TO_PERSIST"):
        ingest_market_data_window(
            provider,
            sink,
            uuid4(),
            request,
            identity_map={},
        )

    assert provider.calls == [request]
    assert sink.calls == []


def test_pipeline_rejects_invalid_dataset_identity_before_provider_call() -> None:
    request, batch, instrument_id = _request_and_batch()
    provider = StubProvider(batch)
    sink = RecordingSink()

    with pytest.raises(TypeError, match="MARKET_DATA_PIPELINE_REQUIRES_DATASET_VERSION_ID"):
        ingest_market_data_window(
            provider,
            sink,
            "not-a-uuid",  # type: ignore[arg-type]
            request,
            identity_map={("TEST", "ABC"): instrument_id},
        )

    assert provider.calls == []
    assert sink.calls == []


def _request_and_batch() -> tuple[MarketDataRequest, ProviderMarketDataBatch, UUID]:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=1)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=end,
        interval=timedelta(minutes=1),
    )
    raw = RawMarketBar(
        source="TEST",
        source_symbol="ABC",
        event_time=start,
        available_time=start,
        ingestion_time=end,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )
    batch = ProviderMarketDataBatch(
        source="TEST",
        request=request,
        bars=(raw,),
        fetched_at=end,
    )
    return request, batch, uuid4()
