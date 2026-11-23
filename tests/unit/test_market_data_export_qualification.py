from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hope.api.market_data_export_qualification import (
    main,
    parse_export_plan,
    qualify_provider_export,
)


PLAN = {
    "schema": "hope.market-data-export-plan.v1",
    "universe_version_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "source": "VENDOR_EXPORT",
    "window": {
        "start": "2026-11-20T14:30:00Z",
        "end": "2026-11-20T14:32:00Z",
        "interval_seconds": 60,
    },
    "instruments": [
        {"source_symbol": "AAA", "instrument_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
        {"source_symbol": "BBB", "instrument_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"},
    ],
}
MULTI_PLAN = {
    "schema": "hope.market-data-export-plan.v2",
    "universe_version_id": PLAN["universe_version_id"],
    "source": PLAN["source"],
    "windows": [
        {
            "start": "2026-11-20T14:30:00Z",
            "end": "2026-11-20T14:31:00Z",
            "interval_seconds": 60,
        },
        {
            "start": "2026-11-20T14:31:00Z",
            "end": "2026-11-20T14:32:00Z",
            "interval_seconds": 60,
        },
    ],
    "instruments": PLAN["instruments"],
}
CSV = (
    "source,source_symbol,event_time,available_time,ingestion_time,effective_time,open,high,low,close,volume\n"
    "VENDOR_EXPORT,AAA,2026-11-20T14:30:00Z,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,,100,102,99,101,10\n"
    "VENDOR_EXPORT,BBB,2026-11-20T14:30:00Z,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,,200,202,199,201,20\n"
    "VENDOR_EXPORT,AAA,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,2026-11-20T14:33:00Z,,101,103,100,102,11\n"
    "VENDOR_EXPORT,BBB,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,2026-11-20T14:33:00Z,,201,203,200,202,21\n"
).encode()


def _files(tmp_path: Path, plan=PLAN):
    plan_path = tmp_path / "plan.json"
    plan_bytes = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    plan_path.write_bytes(plan_bytes)
    export_path = tmp_path / "export.csv"
    export_path.write_bytes(CSV)
    return plan_path, plan_bytes, export_path


def _qualify(tmp_path: Path, plan=PLAN):
    plan_path, plan_bytes, export_path = _files(tmp_path, plan)
    return qualify_provider_export(
        plan_path=plan_path,
        plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
        export_path=export_path,
        export_sha256=hashlib.sha256(CSV).hexdigest(),
        provider_contract_sha256="d" * 64,
        observed_at=datetime(2026, 11, 20, 14, 35, tzinfo=UTC),
    )


def test_exact_export_emits_non_persisting_manifest_bound_receipt(tmp_path):
    receipt = _qualify(tmp_path)
    assert receipt["schema"] == "hope.market-data-export-qualification.v2"
    assert receipt["export_preflight_status"] == "PASS"
    assert receipt["bar_count"] == 4
    assert receipt["persistence_performed"] is False
    assert receipt["paper_authority"] is False
    assert receipt["database_acceptance"] is False
    assert receipt["release_admission"] is False
    assert receipt["universe_version_id"] == PLAN["universe_version_id"]
    assert receipt["window_count"] == 1
    assert len(receipt["windows"]) == 1
    encoded = json.dumps(receipt, sort_keys=True)
    assert str(tmp_path) not in encoded
    assert "101" not in encoded and "201" not in encoded
    assert len(receipt["manifest_sha256"]) == 64


def test_multi_window_plan_qualifies_complete_export_as_one_manifest(tmp_path):
    receipt = _qualify(tmp_path, MULTI_PLAN)
    assert receipt["bar_count"] == 4
    assert receipt["window_count"] == 2
    assert receipt["windows"] == [
        {
            "start": "2026-11-20T14:30:00+00:00",
            "end": "2026-11-20T14:31:00+00:00",
            "interval_seconds": 60,
        },
        {
            "start": "2026-11-20T14:31:00+00:00",
            "end": "2026-11-20T14:32:00+00:00",
            "interval_seconds": 60,
        },
    ]


def test_multi_window_plan_overlap_is_rejected_before_export_read(tmp_path):
    plan = json.loads(json.dumps(MULTI_PLAN))
    plan["windows"][1] = dict(plan["windows"][0])
    plan_path, plan_bytes, export_path = _files(tmp_path, plan)
    export_path.unlink()
    with pytest.raises(ValueError, match="WINDOWS_OVERLAP"):
        qualify_provider_export(
            plan_path=plan_path,
            plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
            export_path=export_path,
            export_sha256=hashlib.sha256(CSV).hexdigest(),
            provider_contract_sha256="d" * 64,
            observed_at=datetime(2026, 11, 20, 14, 35, tzinfo=UTC),
        )


def test_plan_digest_fails_before_export_path_is_opened(tmp_path):
    plan_path, _, _ = _files(tmp_path)
    with pytest.raises(ValueError, match="BOUNDED_READ_DIGEST_MISMATCH"):
        qualify_provider_export(
            plan_path=plan_path,
            plan_sha256="0" * 64,
            export_path=tmp_path / "does-not-exist.csv",
            export_sha256="1" * 64,
            provider_contract_sha256="2" * 64,
            observed_at=datetime(2026, 11, 20, 14, 35, tzinfo=UTC),
        )


def test_changed_export_is_refused(tmp_path):
    plan_path, plan_bytes, export_path = _files(tmp_path)
    export_path.write_bytes(CSV + b"\n")
    with pytest.raises(ValueError, match="FILE_PROVIDER_PAYLOAD_DIGEST_MISMATCH"):
        qualify_provider_export(
            plan_path=plan_path,
            plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
            export_path=export_path,
            export_sha256=hashlib.sha256(CSV).hexdigest(),
            provider_contract_sha256="d" * 64,
            observed_at=datetime(2026, 11, 20, 14, 35, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda plan: plan.update(extra=True), "PLAN_SCHEMA_INVALID"),
        (lambda plan: plan["window"].update(interval_seconds=0), "INTERVAL_INVALID"),
        (lambda plan: plan["instruments"].append(dict(plan["instruments"][0])), "SYMBOL_DUPLICATE"),
        (lambda plan: plan.update(universe_version_id="AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"), "UUID_INVALID"),
    ],
)
def test_plan_schema_and_identity_are_exact(mutate, error):
    plan = json.loads(json.dumps(PLAN))
    mutate(plan)
    with pytest.raises(ValueError, match=error):
        parse_export_plan(json.dumps(plan).encode())


def test_unsafe_plan_file_and_cli_refusal_do_not_emit_receipt(tmp_path, capsys):
    plan_path, plan_bytes, export_path = _files(tmp_path)
    os.chmod(plan_path, 0o666)
    result = main([
        "--plan", str(plan_path),
        "--plan-sha256", hashlib.sha256(plan_bytes).hexdigest(),
        "--export", str(export_path),
        "--export-sha256", hashlib.sha256(CSV).hexdigest(),
        "--provider-contract-sha256", "d" * 64,
        "--observed-at", "2026-11-20T14:35:00Z",
    ])
    captured = capsys.readouterr()
    assert result == 2 and captured.out == ""
    assert "qualification refused" in captured.err
