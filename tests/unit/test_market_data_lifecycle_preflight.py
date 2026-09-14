from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import MarketDataRequest


class NoCallProvider:
    source = "TEST"

    def __init__(self) -> None:
        self.calls = 0

    def fetch_bars(self, request: MarketDataRequest):  # pragma: no cover
        self.calls += 1
        raise AssertionError("provider must not be called")


class NoCallSink:
    def append(self, dataset_version_id, bars):  # pragma: no cover
        raise AssertionError("sink must not be called")


class NoCallFinalizer:
    def finalize(self, dataset_version_id, requests, *, identity_map):  # pragma: no cover
        raise AssertionError("finalizer must not be called")


def test_lifecycle_rejects_nonhomogeneous_windows_before_provider_call() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    first = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    second = MarketDataRequest(
        source="TEST",
        source_symbols=("XYZ",),
        start=first.end,
        end=first.end + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    provider = NoCallProvider()

    with pytest.raises(ValueError, match="WINDOWS_NOT_HOMOGENEOUS"):
        ingest_and_seal_market_data_windows(
            provider,
            NoCallSink(),
            NoCallFinalizer(),
            uuid4(),
            (first, second),
            identity_map={("TEST", "ABC"): uuid4(), ("TEST", "XYZ"): uuid4()},
        )

    assert provider.calls == 0


def test_lifecycle_rejects_invalid_dataset_identity_before_provider_call() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    provider = NoCallProvider()

    with pytest.raises(TypeError, match="LIFECYCLE_REQUIRES_DATASET_VERSION_ID"):
        ingest_and_seal_market_data_windows(
            provider,
            NoCallSink(),
            NoCallFinalizer(),
            "not-a-uuid",  # type: ignore[arg-type]
            (request,),
            identity_map={("TEST", "ABC"): uuid4()},
        )

    assert provider.calls == 0
