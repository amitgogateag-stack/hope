from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from hope.infrastructure.market_data.file_provider import (
    MarketDataFileEvidence,
    VerifiedCsvMarketDataProvider,
)
from hope.infrastructure.market_data.pipeline import ingest_market_data_window
from hope.infrastructure.market_data.provider import MarketDataRequest, fetch_and_normalize_bars


HEADER = "source,source_symbol,event_time,available_time,ingestion_time,effective_time,open,high,low,close,volume\n"
ROWS = (
    "VENDOR_EXPORT,AAA,2026-11-20T14:30:00Z,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,,100,102,99,101,10\n"
    "VENDOR_EXPORT,BBB,2026-11-20T14:30:00Z,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,,200,202,199,201,20\n"
    "VENDOR_EXPORT,AAA,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,2026-11-20T14:33:00Z,,101,103,100,102,11\n"
    "VENDOR_EXPORT,BBB,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,2026-11-20T14:33:00Z,,201,203,200,202,21\n"
)


def request(*, source="VENDOR_EXPORT"):
    return MarketDataRequest(
        source=source,
        source_symbols=("AAA", "BBB"),
        start=datetime(2026, 11, 20, 14, 30, tzinfo=UTC),
        end=datetime(2026, 11, 20, 14, 32, tzinfo=UTC),
        interval=timedelta(minutes=1),
    )


def provider(tmp_path: Path, payload: bytes | None = None, **evidence_changes):
    payload = (HEADER + ROWS).encode() if payload is None else payload
    path = tmp_path / "provider-export.csv"
    path.write_bytes(payload)
    values = dict(
        source="VENDOR_EXPORT",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        provider_contract_sha256="c" * 64,
        observed_at=datetime(2026, 11, 20, 14, 35, tzinfo=UTC),
    )
    values.update(evidence_changes)
    return VerifiedCsvMarketDataProvider(path, MarketDataFileEvidence(**values)), path


def test_exact_digest_export_preserves_original_pit_timestamps_and_normalizes(tmp_path):
    adapter, _ = provider(tmp_path)
    identities = {
        ("VENDOR_EXPORT", "AAA"): uuid4(),
        ("VENDOR_EXPORT", "BBB"): uuid4(),
    }
    batch = fetch_and_normalize_bars(adapter, request(), identity_map=identities)
    assert batch.safe_to_persist and batch.input_count == len(batch.bars) == 4
    assert {bar.available_time for bar in batch.bars} == {
        datetime(2026, 11, 20, 14, 31, tzinfo=UTC),
        datetime(2026, 11, 20, 14, 32, tzinfo=UTC),
    }
    assert {bar.ingestion_time for bar in batch.bars} == {
        datetime(2026, 11, 20, 14, 32, tzinfo=UTC),
        datetime(2026, 11, 20, 14, 33, tzinfo=UTC),
    }
    assert adapter.evidence.provider_contract_sha256 == "c" * 64


def test_exact_export_flows_through_authoritative_pipeline_to_sink(tmp_path):
    adapter, _ = provider(tmp_path)
    identities = {
        ("VENDOR_EXPORT", "AAA"): uuid4(),
        ("VENDOR_EXPORT", "BBB"): uuid4(),
    }
    calls = []

    class Sink:
        def append(self, dataset_version_id, bars):
            calls.append((dataset_version_id, bars))

    version = uuid4()
    batch = ingest_market_data_window(
        adapter,
        Sink(),
        version,
        request(),
        identity_map=identities,
    )
    assert batch.safe_to_persist
    assert calls == [(version, batch.bars)]


def test_digest_failure_never_touches_persistence_sink(tmp_path):
    adapter, path = provider(tmp_path)
    path.write_bytes((HEADER + ROWS + "\n").encode())

    class Sink:
        def append(self, dataset_version_id, bars):  # pragma: no cover
            raise AssertionError("unsafe evidence reached persistence")

    with pytest.raises(ValueError, match="FILE_PROVIDER_PAYLOAD_DIGEST_MISMATCH"):
        ingest_market_data_window(
            adapter,
            Sink(),
            uuid4(),
            request(),
            identity_map={
                ("VENDOR_EXPORT", "AAA"): uuid4(),
                ("VENDOR_EXPORT", "BBB"): uuid4(),
            },
        )


def test_changed_payload_is_rejected_before_csv_interpretation(tmp_path):
    adapter, path = provider(tmp_path)
    path.write_bytes((HEADER + ROWS.replace("101,10", "999,10", 1)).encode())
    with pytest.raises(ValueError, match="FILE_PROVIDER_PAYLOAD_DIGEST_MISMATCH"):
        adapter.fetch_bars(request())


@pytest.mark.parametrize("field", ["payload_sha256", "provider_contract_sha256"])
def test_unpinned_payload_or_contract_is_rejected(tmp_path, field):
    with pytest.raises(ValueError, match="FILE_PROVIDER_EVIDENCE_DIGEST_INVALID"):
        provider(tmp_path, **{field: "A" * 64})


@pytest.mark.parametrize(
    "payload",
    [
        (HEADER.replace(",volume", "") + ROWS).encode(),
        (HEADER.replace("source,", "unexpected,source,") + ROWS).encode(),
        b"\xef\xbb\xbf" + (HEADER + ROWS).encode(),
    ],
)
def test_schema_mismatch_and_bom_are_rejected(tmp_path, payload):
    adapter, _ = provider(tmp_path, payload)
    with pytest.raises(ValueError, match="FILE_PROVIDER_SCHEMA_INVALID"):
        adapter.fetch_bars(request())


def test_naive_or_non_rfc3339_timestamp_is_rejected(tmp_path):
    payload = (HEADER + ROWS.replace("2026-11-20T14:30:00Z", "2026-11-20 14:30:00", 1)).encode()
    adapter, _ = provider(tmp_path, payload)
    with pytest.raises(ValueError, match="FILE_PROVIDER_TIMESTAMP_INVALID"):
        adapter.fetch_bars(request())


@pytest.mark.parametrize(
    "replacement,error",
    [
        (ROWS.splitlines()[0] + "\n", "PROVIDER_RESPONSE_INCOMPLETE"),
        (ROWS + ROWS.splitlines()[0] + "\n", "FILE_PROVIDER_ROW_COUNT_EXCEEDED"),
        (ROWS.replace("VENDOR_EXPORT,AAA", "VENDOR_EXPORT,ZZZ", 1), "PROVIDER_RETURNED_UNREQUESTED_SYMBOL"),
    ],
)
def test_partial_excess_and_unrequested_exports_fail_closed(tmp_path, replacement, error):
    adapter, _ = provider(tmp_path, (HEADER + replacement).encode())
    with pytest.raises(ValueError, match=error):
        adapter.fetch_bars(request())


def test_request_source_must_match_pinned_export_source(tmp_path):
    adapter, _ = provider(tmp_path)
    with pytest.raises(ValueError, match="FILE_PROVIDER_REQUEST_SOURCE_MISMATCH"):
        adapter.fetch_bars(request(source="OTHER"))


def test_export_observation_must_follow_closed_window(tmp_path):
    adapter, _ = provider(
        tmp_path,
        observed_at=datetime(2026, 11, 20, 14, 31, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="PROVIDER_FETCH_PRECEDES_CLOSED_WINDOW"):
        adapter.fetch_bars(request())


def test_size_and_private_regular_file_boundary_is_enforced(tmp_path):
    payload = (HEADER + ROWS).encode()
    adapter, path = provider(tmp_path, payload, max_bytes=len(payload) - 1)
    with pytest.raises(ValueError, match="BOUNDED_READ_UNTRUSTED_FILE"):
        adapter.fetch_bars(request())
    adapter, path = provider(tmp_path, payload)
    os.chmod(path, 0o666)
    with pytest.raises(ValueError, match="BOUNDED_READ_UNTRUSTED_FILE"):
        adapter.fetch_bars(request())


def test_symlink_cannot_substitute_for_pinned_export(tmp_path):
    adapter, path = provider(tmp_path)
    link = tmp_path / "provider-link.csv"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("symlink unavailable")
    linked = VerifiedCsvMarketDataProvider(link, adapter.evidence)
    with pytest.raises(ValueError, match="BOUNDED_READ_NOT_REGULAR"):
        linked.fetch_bars(request())
