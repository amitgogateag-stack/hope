import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.infrastructure.repositories.market_contexts import (
    _verify_requested_instruments,
    _verify_read_side_coverage,
    _verify_read_side_evidence,
    _verify_read_side_membership,
)


def _dataset_evidence() -> dict[str, object]:
    universe_version_id = uuid4()
    instrument_id = uuid4()
    start = datetime(2026, 9, 15, 14, 30, tzinfo=timezone.utc)
    manifest = {
        "version": 2,
        "universe_version_id": str(universe_version_id),
        "windows": [
            {
                "source": "TEST",
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=1)).isoformat(),
                "interval_seconds": 60,
                "instruments": [
                    {
                        "source_symbol": "READ",
                        "instrument_id": str(instrument_id),
                    }
                ],
            }
        ],
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return {
        "source": "TEST",
        "manifest": manifest,
        "manifest_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "universe_version_id": universe_version_id,
        "universe_pit_certified": True,
        "declared_member_count": 1,
        "actual_member_count": 1,
    }


def test_read_side_accepts_canonical_manifest_evidence() -> None:
    evidence = _dataset_evidence()
    expected_keys = _verify_read_side_evidence(evidence)
    assert len(expected_keys) == 1


def test_read_side_rejects_manifest_checksum_mismatch() -> None:
    evidence = _dataset_evidence()
    evidence["manifest_hash"] = "0" * 64
    with pytest.raises(ValueError, match="MANIFEST_CHECKSUM_MISMATCH"):
        _verify_read_side_evidence(evidence)


def test_read_side_rejects_manifest_universe_mismatch() -> None:
    evidence = _dataset_evidence()
    evidence["universe_version_id"] = uuid4()
    with pytest.raises(ValueError, match="MANIFEST_UNIVERSE_MISMATCH"):
        _verify_read_side_evidence(evidence)


def test_read_side_rejects_untrusted_universe_state() -> None:
    evidence = _dataset_evidence()
    evidence["universe_pit_certified"] = False
    with pytest.raises(ValueError, match="REQUIRES_PIT_CERTIFIED_UNIVERSE"):
        _verify_read_side_evidence(evidence)

    evidence = _dataset_evidence()
    evidence["actual_member_count"] = 0
    with pytest.raises(ValueError, match="UNIVERSE_CARDINALITY_MISMATCH"):
        _verify_read_side_evidence(evidence)


def test_read_side_reparses_manifest_against_dataset_source() -> None:
    evidence = _dataset_evidence()
    evidence["source"] = "OTHER"
    with pytest.raises(ValueError, match="MANIFEST_DATASET_SOURCE_MISMATCH"):
        _verify_read_side_evidence(evidence)


def test_read_side_rejects_manifest_key_outside_universe_membership() -> None:
    expected_keys = _verify_read_side_evidence(_dataset_evidence())
    instrument_id, event_time = next(iter(expected_keys))

    with pytest.raises(ValueError, match="UNIVERSE_MEMBERSHIP_MISMATCH"):
        _verify_read_side_membership(
            expected_keys,
            {
                instrument_id: (
                    event_time + timedelta(seconds=1),
                    None,
                )
            },
        )


def test_read_side_rejects_requested_instrument_outside_manifest() -> None:
    expected_keys = _verify_read_side_evidence(_dataset_evidence())
    declared_instrument_id, _ = next(iter(expected_keys))

    _verify_requested_instruments(expected_keys, (declared_instrument_id,))
    with pytest.raises(ValueError, match="INSTRUMENT_OUTSIDE_MANIFEST"):
        _verify_requested_instruments(expected_keys, (uuid4(),))


def test_read_side_rejects_inexact_persisted_coverage() -> None:
    expected_keys = _verify_read_side_evidence(_dataset_evidence())
    exact_keys = tuple(expected_keys)
    _, event_time = exact_keys[0]

    for persisted_keys in (
        (),
        exact_keys + exact_keys,
        exact_keys + ((uuid4(), event_time),),
    ):
        with pytest.raises(ValueError, match="PERSISTED_COVERAGE_MISMATCH"):
            _verify_read_side_coverage(expected_keys, persisted_keys)

    _verify_read_side_coverage(expected_keys, exact_keys)
