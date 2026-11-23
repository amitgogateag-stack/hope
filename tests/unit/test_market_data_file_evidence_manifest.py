from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from hope.infrastructure.market_data.file_provider import (
    MarketDataFileEvidence,
    VerifiedCsvMarketDataProvider,
    market_data_file_evidence_payload,
)
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.market_data.recovery import ingest_market_data_recovery_windows
from hope.infrastructure.repositories.market_data_manifest import (
    build_market_data_manifest,
    manifest_evidence,
    manifest_file_evidence,
    validate_manifest_file_evidence,
)


HEADER = "source,source_symbol,event_time,available_time,ingestion_time,effective_time,open,high,low,close,volume\n"
ROW = "TEST,ABC,2026-11-20T14:30:00Z,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,,100,101,99,100,10\n"


def evidence(payload: bytes = (HEADER + ROW).encode(), **changes) -> MarketDataFileEvidence:
    values = dict(
        source="TEST",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        provider_contract_sha256="c" * 64,
        observed_at=datetime(2026, 11, 20, 14, 35, tzinfo=UTC),
    )
    values.update(changes)
    return MarketDataFileEvidence(**values)


def request() -> MarketDataRequest:
    start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
    return MarketDataRequest(
        source="TEST", source_symbols=("ABC",), start=start,
        end=start + timedelta(minutes=1), interval=timedelta(minutes=1),
    )


def test_manifest_canonically_binds_file_and_provider_contract_identity():
    instrument = uuid4()
    pinned = evidence(max_bytes=7)
    manifest = build_market_data_manifest(
        uuid4(), (request(),), identity_map={("TEST", "ABC"): instrument},
        provider_evidence=pinned,
    )
    assert manifest["provider_evidence"] == market_data_file_evidence_payload(pinned)
    restored = manifest_file_evidence(manifest)
    assert restored is not None
    assert market_data_file_evidence_payload(restored) == market_data_file_evidence_payload(pinned)
    assert restored.max_bytes == 32 * 1024 * 1024  # runtime read bound is not provenance
    assert manifest_evidence(manifest)[0] == {(instrument, request().start)}
    validate_manifest_file_evidence(manifest, pinned)


def test_manifest_rejects_provider_evidence_from_another_source():
    with pytest.raises(ValueError, match="PROVIDER_EVIDENCE_SOURCE_MISMATCH"):
        build_market_data_manifest(
            uuid4(), (request(),), identity_map={("TEST", "ABC"): uuid4()},
            provider_evidence=evidence(source="OTHER"),
        )


def test_manifest_rejects_export_observed_before_declared_window_closed():
    with pytest.raises(ValueError, match="PROVIDER_EVIDENCE_PRECEDES_WINDOW"):
        build_market_data_manifest(
            uuid4(), (request(),), identity_map={("TEST", "ABC"): uuid4()},
            provider_evidence=evidence(
                observed_at=datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
            ),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "hope.market-data-file-evidence.v0"},
        {"payload_sha256": "A" * 64},
        {"observed_at": "2026-11-20T14:35:00Z"},
        {"extra": "field"},
    ],
)
def test_manifest_parser_rejects_noncanonical_or_unknown_evidence(change):
    manifest = build_market_data_manifest(
        uuid4(), (request(),), identity_map={("TEST", "ABC"): uuid4()},
        provider_evidence=evidence(),
    )
    manifest["provider_evidence"] = {**manifest["provider_evidence"], **change}
    with pytest.raises(ValueError, match="FILE_PROVIDER_MANIFEST_EVIDENCE_INVALID"):
        manifest_evidence(manifest)


def test_runtime_evidence_must_exactly_match_immutable_manifest():
    manifest = build_market_data_manifest(
        uuid4(), (request(),), identity_map={("TEST", "ABC"): uuid4()},
        provider_evidence=evidence(),
    )
    with pytest.raises(ValueError, match="PROVIDER_EVIDENCE_MISMATCH"):
        validate_manifest_file_evidence(manifest, None)
    with pytest.raises(ValueError, match="PROVIDER_EVIDENCE_MISMATCH"):
        validate_manifest_file_evidence(manifest, evidence(provider_contract_sha256="d" * 64))


def test_file_provider_evidence_crosses_preflight_and_final_seal(tmp_path):
    payload = (HEADER + ROW).encode()
    path = tmp_path / "export.csv"
    path.write_bytes(payload)
    pinned = evidence(payload)
    provider = VerifiedCsvMarketDataProvider(path, pinned)
    version, instrument = uuid4(), uuid4()
    calls = []

    class Sink:
        def append(self, dataset_version_id, bars):
            calls.append(("append", dataset_version_id, bars))

    class Finalizer:
        def preflight(self, dataset_version_id, requests, *, identity_map, provider_evidence):
            calls.append(("preflight", provider_evidence))

        def finalize(self, dataset_version_id, requests, *, identity_map, provider_evidence):
            calls.append(("finalize", provider_evidence))

    result = ingest_and_seal_market_data_windows(
        provider, Sink(), Finalizer(), version, (request(),),
        identity_map={("TEST", "ABC"): instrument},
    )
    assert result[0].safe_to_persist
    assert calls[0] == ("preflight", pinned)
    assert calls[1][0:2] == ("append", version)
    assert calls[2] == ("finalize", pinned)


def test_file_provider_recovery_requires_same_evidence_before_read(tmp_path):
    payload = (HEADER + ROW).encode()
    path = tmp_path / "export.csv"
    path.write_bytes(payload)
    pinned = evidence(payload)
    provider = VerifiedCsvMarketDataProvider(path, pinned)
    calls = []

    class Authorizer:
        def preflight_subset(self, dataset_version_id, requests, *, identity_map, provider_evidence):
            calls.append(provider_evidence)

    class Sink:
        def append(self, dataset_version_id, bars):
            calls.append(bars)

    result = ingest_market_data_recovery_windows(
        provider, Sink(), Authorizer(), uuid4(), (request(),),
        identity_map={("TEST", "ABC"): uuid4()},
    )
    assert result[0].safe_to_persist
    assert calls[0] == pinned
    assert len(calls[1]) == 1
