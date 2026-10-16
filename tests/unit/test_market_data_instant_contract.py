from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from hope.domain.market_data.models import MarketBar
from hope.infrastructure.market_data.ingestion import RawMarketBar, normalize_source_bars
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import (
    MarketDataRequest, ProviderMarketDataBatch, fetch_and_normalize_bars,
)


NY = ZoneInfo("America/New_York")
HOUR = timedelta(hours=1)


def local(hour, *, fold=0, month=11, day=1, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=NY, fold=fold)


def request(start=None, end=None):
    return MarketDataRequest("TEST", ("ABC",), start or local(0), end or local(3), HOUR)


def raw(event, **overrides):
    return replace(RawMarketBar(
        "TEST", "ABC", event, event, local(4), "100", "101", "99", "100", "1",
    ), **overrides)


def model(event, available, ingestion):
    return MarketBar(instrument_id="test", event_time=event, available_time=available,
                     ingestion_time=ingestion, open=100, high=101, low=99, close=100, volume=1)


@pytest.mark.parametrize("month,day,expected", [(11, 1, 4), (3, 8, 2)])
def test_request_grid_measures_elapsed_hours_and_preserves_original_window(month, day, expected):
    start, end = local(0, month=month, day=day), local(3, month=month, day=day)
    asked = request(start, end)
    assert asked.start is start and asked.end is end
    assert asked.expected_keys == tuple(
        ("ABC", start.astimezone(UTC) + i * HOUR) for i in range(expected)
    )


def test_equal_wall_clock_fold_window_is_one_elapsed_hour():
    asked = request(local(1, fold=0), local(1, fold=1))
    assert asked.expected_keys == (("ABC", datetime(2026, 11, 1, 5, tzinfo=UTC)),)
    with pytest.raises(ValueError, match="WINDOW_INVALID"):
        request(local(1, fold=1), local(1, fold=0))


def test_alignment_uses_elapsed_duration():
    with pytest.raises(ValueError, match="NOT_ALIGNED"):
        MarketDataRequest("TEST", ("ABC",), local(0), local(3), timedelta(hours=3))


def test_provider_accepts_both_folds_and_mixed_timezone_representations():
    asked = request()
    bars = tuple(raw(t.astimezone(NY)) for _, t in asked.expected_keys)
    batch = ProviderMarketDataBatch("TEST", asked, bars, local(3))
    assert len(batch.bars) == 4
    result = normalize_source_bars(tuple(reversed(bars)), identity_map={("TEST", "ABC"): uuid4()})
    assert result.safe_to_persist
    assert tuple(b.event_time.astimezone(UTC) for b in result.bars) == tuple(t for _, t in asked.expected_keys)


def test_same_instant_aliases_are_duplicates_and_all_are_quarantined():
    asked = request(local(1, fold=0), local(1, fold=1))
    event = local(1, fold=0)
    bars = (raw(event), raw(event.astimezone(UTC)))
    with pytest.raises(ValueError, match="DUPLICATE_BAR_KEY"):
        ProviderMarketDataBatch("TEST", asked, bars, local(3))
    normalized = normalize_source_bars(bars, identity_map={("TEST", "ABC"): uuid4()})
    assert not normalized.bars and not normalized.safe_to_persist
    assert [r.reason.value for r in normalized.rejections] == ["DUPLICATE_SOURCE_BAR"] * 2


@pytest.mark.parametrize("event,error", [
    (local(1, fold=0), "OUTSIDE_REQUEST_WINDOW"),
    (local(1, fold=1, minute=30), "OFF_INTERVAL_GRID"),
])
def test_provider_bounds_and_grid_compare_instants(event, error):
    asked = request(local(1, fold=1), local(3))
    with pytest.raises(ValueError, match=error):
        ProviderMarketDataBatch("TEST", asked, (raw(event),), local(3))


def test_fetch_time_in_earlier_fold_cannot_close_later_fold_window():
    asked = request(local(0), local(1, fold=1))
    with pytest.raises(ValueError, match="FETCH_PRECEDES"):
        ProviderMarketDataBatch("TEST", asked, (), local(1, fold=0, minute=30))


def test_provider_echo_cannot_substitute_a_different_fold():
    asked = request(local(1, fold=0), local(3))
    echoed = request(local(1, fold=1), local(3))
    batch = ProviderMarketDataBatch("TEST", echoed,
        tuple(raw(t.astimezone(NY)) for _, t in echoed.expected_keys), local(3))

    class Provider:
        source = "TEST"

        def fetch_bars(self, value):
            return batch

    with pytest.raises(ValueError, match="REQUEST_ECHO_MISMATCH"):
        fetch_and_normalize_bars(Provider(), asked, identity_map={("TEST", "ABC"): uuid4()})


@pytest.mark.parametrize("event,available,ingestion,error", [
    (local(1, fold=1, minute=30), local(1, fold=0, minute=45), local(3), "available_time"),
    (local(0), local(1, fold=1, minute=30), local(1, fold=0, minute=45), "ingestion_time"),
])
def test_market_bar_rejects_reverse_instant_chronology(event, available, ingestion, error):
    with pytest.raises(ValidationError, match=error):
        model(event, available, ingestion)


def test_market_bar_accepts_valid_chronology_despite_reversed_wall_clock():
    event, available = local(1, fold=0, minute=45), local(1, fold=1, minute=15)
    bar = model(event, available, local(3))
    assert bar.event_time is event and bar.available_time is available


def test_ingestion_quarantines_reverse_instant_chronology():
    normalized = normalize_source_bars(
        (raw(local(1, fold=1, minute=30), available_time=local(1, fold=0, minute=45)),),
        identity_map={("TEST", "ABC"): uuid4()},
    )
    assert not normalized.safe_to_persist and not normalized.bars
    assert normalized.rejections[0].reason.value == "INVALID_MARKET_BAR"


@pytest.mark.parametrize("second_start,error", [
    (local(1, fold=1), "WINDOWS_NOT_CONTIGUOUS"),
    (local(1, fold=0), "WINDOWS_OVERLAP_OR_OUT_OF_ORDER"),
])
def test_lifecycle_rejects_fold_gap_or_overlap_before_any_side_effect(second_start, error):
    boundary = local(1, fold=0) if "NOT_CONTIGUOUS" in error else local(1, fold=1)
    windows = (request(local(0), boundary), request(second_start, local(3)))

    class NoCalls:
        def __getattr__(self, name):
            pytest.fail(f"Unexpected side effect: {name}")

    with pytest.raises(ValueError, match=error):
        ingest_and_seal_market_data_windows(NoCalls(), NoCalls(), NoCalls(), uuid4(), windows,
                                           identity_map={("TEST", "ABC"): uuid4()})


def test_lifecycle_accepts_true_contiguity_with_reversed_local_clock():
    boundary = local(1, fold=1)
    windows = (request(local(0), boundary), request(boundary.astimezone(UTC), local(3)))
    calls = []

    class Provider:
        source = "TEST"

        def fetch_bars(self, value):
            return ProviderMarketDataBatch("TEST", value,
                tuple(raw(t.astimezone(NY)) for _, t in value.expected_keys), local(4))

    class Sink:
        def append(self, version, bars):
            calls.append(len(bars))

    class Finalizer:
        def preflight(self, *args, **kwargs):
            calls.append("preflight")

        def finalize(self, *args, **kwargs):
            calls.append("sealed")

    ingest_and_seal_market_data_windows(Provider(), Sink(), Finalizer(), uuid4(), windows,
                                       identity_map={("TEST", "ABC"): uuid4()})
    assert calls == ["preflight", 2, 2, "sealed"]
