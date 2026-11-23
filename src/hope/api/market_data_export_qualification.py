"""Offline qualification for one digest-pinned provider export.

This command validates evidence and emits a deterministic receipt.  It never
connects to PostgreSQL, persists bars, grants PAPER authority, or contacts a
provider.  A later reviewed database operation must independently consume the
same manifest and evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping
from uuid import UUID

from hope.infrastructure.durable_file import authenticated_bounded_regular_read
from hope.infrastructure.market_data.file_provider import (
    MarketDataFileEvidence,
    VerifiedCsvMarketDataProvider,
)
from hope.infrastructure.market_data.provider import (
    MarketDataRequest,
    fetch_and_normalize_bars,
)
from hope.infrastructure.repositories.market_data_manifest import build_market_data_manifest


_DIGEST = re.compile(r"[0-9a-f]{64}")
_RFC3339 = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)
_PLAN_KEYS = {"schema", "universe_version_id", "source", "window", "instruments"}
_WINDOW_KEYS = {"start", "end", "interval_seconds"}
_INSTRUMENT_KEYS = {"source_symbol", "instrument_id"}


def _instant(value: object) -> datetime:
    if type(value) is not str or _RFC3339.fullmatch(value) is None:
        raise ValueError("MARKET_DATA_QUALIFICATION_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise ValueError("MARKET_DATA_QUALIFICATION_TIMESTAMP_INVALID") from error
    if parsed.utcoffset() is None:
        raise ValueError("MARKET_DATA_QUALIFICATION_TIMESTAMP_INVALID")
    return parsed.astimezone(UTC)


def _uuid(value: object) -> UUID:
    if type(value) is not str:
        raise ValueError("MARKET_DATA_QUALIFICATION_UUID_INVALID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError("MARKET_DATA_QUALIFICATION_UUID_INVALID") from error
    if str(parsed) != value:
        raise ValueError("MARKET_DATA_QUALIFICATION_UUID_INVALID")
    return parsed


@dataclass(frozen=True)
class MarketDataExportPlan:
    universe_version_id: UUID
    request: MarketDataRequest
    identity_map: Mapping[tuple[str, str], UUID]


def parse_export_plan(payload: bytes) -> MarketDataExportPlan:
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("MARKET_DATA_QUALIFICATION_PLAN_INVALID") from error
    if type(value) is not dict or set(value) != _PLAN_KEYS:
        raise ValueError("MARKET_DATA_QUALIFICATION_PLAN_INVALID")
    if value["schema"] != "hope.market-data-export-plan.v1":
        raise ValueError("MARKET_DATA_QUALIFICATION_PLAN_SCHEMA_INVALID")
    source = value["source"]
    if type(source) is not str or not source or source != source.strip():
        raise ValueError("MARKET_DATA_QUALIFICATION_SOURCE_INVALID")
    window = value["window"]
    if type(window) is not dict or set(window) != _WINDOW_KEYS:
        raise ValueError("MARKET_DATA_QUALIFICATION_WINDOW_INVALID")
    interval = window["interval_seconds"]
    if type(interval) is not int or not 1 <= interval <= 86400:
        raise ValueError("MARKET_DATA_QUALIFICATION_INTERVAL_INVALID")
    instruments = value["instruments"]
    if type(instruments) is not list or not 1 <= len(instruments) <= 2048:
        raise ValueError("MARKET_DATA_QUALIFICATION_INSTRUMENTS_INVALID")
    identities: dict[tuple[str, str], UUID] = {}
    for instrument in instruments:
        if type(instrument) is not dict or set(instrument) != _INSTRUMENT_KEYS:
            raise ValueError("MARKET_DATA_QUALIFICATION_INSTRUMENTS_INVALID")
        symbol = instrument["source_symbol"]
        if type(symbol) is not str or not symbol or symbol != symbol.strip():
            raise ValueError("MARKET_DATA_QUALIFICATION_SYMBOL_INVALID")
        key = (source, symbol)
        if key in identities:
            raise ValueError("MARKET_DATA_QUALIFICATION_SYMBOL_DUPLICATE")
        identities[key] = _uuid(instrument["instrument_id"])
    symbols = tuple(sorted(symbol for _, symbol in identities))
    request = MarketDataRequest(
        source=source,
        source_symbols=symbols,
        start=_instant(window["start"]),
        end=_instant(window["end"]),
        interval=timedelta(seconds=interval),
    )
    if request.expected_count > 1_000_000:
        raise ValueError("MARKET_DATA_QUALIFICATION_PLAN_TOO_LARGE")
    return MarketDataExportPlan(
        universe_version_id=_uuid(value["universe_version_id"]),
        request=request,
        identity_map=identities,
    )


def qualify_provider_export(
    *,
    plan_path: str | Path,
    plan_sha256: str,
    export_path: str | Path,
    export_sha256: str,
    provider_contract_sha256: str,
    observed_at: datetime,
) -> dict[str, object]:
    if _DIGEST.fullmatch(plan_sha256) is None:
        raise ValueError("MARKET_DATA_QUALIFICATION_PLAN_DIGEST_INVALID")
    plan_payload = authenticated_bounded_regular_read(
        plan_path, 1024 * 1024, expected_sha256=plan_sha256
    )
    plan = parse_export_plan(plan_payload)
    evidence = MarketDataFileEvidence(
        source=plan.request.source,
        payload_sha256=export_sha256,
        provider_contract_sha256=provider_contract_sha256,
        observed_at=observed_at,
    )
    provider = VerifiedCsvMarketDataProvider(export_path, evidence)
    batch = fetch_and_normalize_bars(
        provider, plan.request, identity_map=plan.identity_map
    )
    manifest = build_market_data_manifest(
        plan.universe_version_id,
        (plan.request,),
        identity_map=plan.identity_map,
        provider_evidence=evidence,
    )
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "schema": "hope.market-data-export-qualification.v1",
        "export_preflight_status": "PASS",
        "persistence_performed": False,
        "paper_authority": False,
        "database_acceptance": False,
        "release_admission": False,
        "source": plan.request.source,
        "universe_version_id": str(plan.universe_version_id),
        "request_plan_sha256": plan_sha256,
        "payload_sha256": evidence.payload_sha256,
        "provider_contract_sha256": evidence.provider_contract_sha256,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "bar_count": len(batch.bars),
        "window": {
            "start": plan.request.start.astimezone(UTC).isoformat(),
            "end": plan.request.end.astimezone(UTC).isoformat(),
            "interval_seconds": int(plan.request.interval.total_seconds()),
        },
        "observed_at": evidence.observed_at.isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline verification of one pinned market-data export; never persists"
    )
    parser.add_argument("--plan", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--export", required=True)
    parser.add_argument("--export-sha256", required=True)
    parser.add_argument("--provider-contract-sha256", required=True)
    parser.add_argument("--observed-at", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = qualify_provider_export(
            plan_path=args.plan,
            plan_sha256=args.plan_sha256,
            export_path=args.export,
            export_sha256=args.export_sha256,
            provider_contract_sha256=args.provider_contract_sha256,
            observed_at=_instant(args.observed_at),
        )
    except (OSError, ValueError, TypeError) as error:
        print(f"qualification refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
