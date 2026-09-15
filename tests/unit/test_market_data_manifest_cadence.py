from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.repositories.market_data_manifest import (
    build_market_data_manifest,
    manifest_expected_keys_for_requests,
    validate_manifest_membership,
    validate_manifest_request_subset,
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


def test_manifest_parser_rejects_dataset_source_mismatch() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    instrument_id = uuid4()
    manifest = build_market_data_manifest(
        uuid4(),
        (request,),
        identity_map={("TEST", "ABC"): instrument_id},
    )

    with pytest.raises(ValueError, match="MANIFEST_DATASET_SOURCE_MISMATCH"):
        manifest_expected_keys_for_requests(
            manifest,
            (request,),
            identity_map={("TEST", "ABC"): instrument_id},
            expected_source="OTHER",
        )


def test_manifest_membership_rejects_undeclared_instrument() -> None:
    with pytest.raises(ValueError, match="INSTRUMENT_NOT_IN_UNIVERSE"):
        validate_manifest_membership(
            {(uuid4(), datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc))},
            memberships={},
        )


def test_manifest_membership_rejects_event_outside_valid_interval() -> None:
    instrument_id = uuid4()
    event_time = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="OUTSIDE_UNIVERSE_MEMBERSHIP"):
        validate_manifest_membership(
            {(instrument_id, event_time)},
            memberships={
                instrument_id: (event_time + timedelta(minutes=1), None),
            },
        )


def test_manifest_recovery_subset_preserves_declared_cadence() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    instrument_id = uuid4()
    declared = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    wider_cadence = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=2),
        interval=timedelta(minutes=2),
    )
    manifest = build_market_data_manifest(
        uuid4(),
        (declared,),
        identity_map={("TEST", "ABC"): instrument_id},
    )

    with pytest.raises(ValueError, match="RECOVERY_REQUEST_CONTRACT_MISMATCH"):
        validate_manifest_request_subset(
            manifest,
            (wider_cadence,),
            identity_map={("TEST", "ABC"): instrument_id},
        )


def test_manifest_builder_rejects_overlapping_logical_coverage() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=2),
        interval=timedelta(minutes=1),
    )
    overlapping = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start + timedelta(minutes=1),
        end=start + timedelta(minutes=3),
        interval=timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="DUPLICATE_LOGICAL_KEY"):
        build_market_data_manifest(
            uuid4(),
            (request, overlapping),
            identity_map={("TEST", "ABC"): uuid4()},
        )


def test_manifest_parser_rejects_overlapping_logical_coverage() -> None:
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
    manifest["windows"].append(dict(manifest["windows"][0]))

    from hope.infrastructure.repositories.market_data_manifest import manifest_expected_keys

    with pytest.raises(ValueError, match="DUPLICATE_LOGICAL_KEY"):
        manifest_expected_keys(manifest)


@pytest.mark.parametrize("interval_seconds", ("60", 60.5, True))
def test_manifest_parser_rejects_noninteger_json_cadence(interval_seconds: object) -> None:
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
    manifest["windows"][0]["interval_seconds"] = interval_seconds

    from hope.infrastructure.repositories.market_data_manifest import manifest_expected_keys

    with pytest.raises(ValueError, match="MARKET_DATA_MANIFEST_INVALID"):
        manifest_expected_keys(manifest)


def test_manifest_parser_rejects_noncanonical_universe_version_id() -> None:
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
    manifest["universe_version_id"] = "{" + str(manifest["universe_version_id"]) + "}"

    from hope.infrastructure.repositories.market_data_manifest import manifest_expected_keys

    with pytest.raises(ValueError, match="MARKET_DATA_MANIFEST_INVALID"):
        manifest_expected_keys(manifest)


def test_manifest_parser_rejects_noncanonical_timestamp_text() -> None:
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
    manifest["windows"][0]["start"] = str(
        manifest["windows"][0]["start"]
    ).replace("+00:00", "Z")

    from hope.infrastructure.repositories.market_data_manifest import manifest_expected_keys

    with pytest.raises(ValueError, match="MARKET_DATA_MANIFEST_INVALID"):
        manifest_expected_keys(manifest)


def test_manifest_builder_rejects_source_aliases_for_one_instrument() -> None:
    start = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    request = MarketDataRequest(
        source="TEST",
        source_symbols=("ABC", "XYZ"),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )
    instrument_id = uuid4()

    with pytest.raises(ValueError, match="DUPLICATE_LOGICAL_KEY"):
        build_market_data_manifest(
            uuid4(),
            (request,),
            identity_map={
                ("TEST", "ABC"): instrument_id,
                ("TEST", "XYZ"): instrument_id,
            },
        )
