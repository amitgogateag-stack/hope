from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.repositories.market_data_manifest import (
    build_market_data_manifest,
    manifest_expected_keys_for_requests,
)


def test_manifest_rejects_fractional_second_cadence_without_truncation() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(seconds=1),
        interval=timedelta(milliseconds=500),
    )

    with pytest.raises(ValueError, match="INTERVAL_REQUIRES_WHOLE_SECONDS"):
        build_market_data_manifest(
            uuid4(),
            (request,),
            identity_map={("TEST", "ABC"): uuid4()},
        )


def test_manifest_parser_rejects_duplicate_logical_instruments() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    manifest = build_market_data_manifest(
        uuid4(),
        (request,),
        identity_map={("TEST", "ABC"): uuid4()},
    )
    window = manifest["windows"][0]
    window["instruments"].append(dict(window["instruments"][0]))

    from hope.infrastructure.repositories.market_data_manifest import manifest_expected_keys

    with pytest.raises(ValueError, match="MARKET_DATA_MANIFEST_INVALID"):
        manifest_expected_keys(manifest)


def test_manifest_parser_rejects_partial_final_cadence_slot() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    manifest = build_market_data_manifest(
        uuid4(),
        (request,),
        identity_map={("TEST", "ABC"): uuid4()},
    )
    manifest["windows"][0]["end"] = (start + timedelta(seconds=90)).isoformat()

    from hope.infrastructure.repositories.market_data_manifest import manifest_expected_keys

    with pytest.raises(ValueError, match="MARKET_DATA_MANIFEST_INVALID"):
        manifest_expected_keys(manifest)


def test_manifest_parser_rejects_symbol_identity_drift_between_windows() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    requests = tuple(
        MarketDataRequest(
            source="TEST",
            source_symbols=("ABC",),
            start=window_start,
            end=window_start + timedelta(minutes=1),
            interval=timedelta(minutes=1),
        )
        for window_start in (start, start + timedelta(minutes=1))
    )
    manifest = build_market_data_manifest(
        uuid4(),
        requests,
        identity_map={("TEST", "ABC"): uuid4()},
    )
    manifest["windows"][1]["instruments"][0]["instrument_id"] = str(uuid4())

    from hope.infrastructure.repositories.market_data_manifest import manifest_expected_keys

    with pytest.raises(ValueError, match="IDENTITY_BINDING_CONFLICT"):
        manifest_expected_keys(manifest)


def test_manifest_request_rejects_swapped_source_identity_bindings() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC", "XYZ"),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    abc_id = uuid4()
    xyz_id = uuid4()
    manifest = build_market_data_manifest(
        uuid4(),
        (request,),
        identity_map={("TEST", "ABC"): abc_id, ("TEST", "XYZ"): xyz_id},
    )

    with pytest.raises(ValueError, match="IDENTITY_BINDING_MISMATCH"):
        manifest_expected_keys_for_requests(
            manifest,
            (request,),
            identity_map={("TEST", "ABC"): xyz_id, ("TEST", "XYZ"): abc_id},
        )
