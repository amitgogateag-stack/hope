"""Digest-bound adapter for operator-supplied provider OHLCV exports.

This adapter does not attest a vendor, license, symbol map, or market calendar.
It only turns one independently identified, immutable export into the existing
fail-closed provider boundary without inventing timestamps or numeric values.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from hope.infrastructure.durable_file import bounded_regular_read
from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.provider import (
    MarketDataRequest,
    ProviderMarketDataBatch,
)


_DIGEST = re.compile(r"[0-9a-f]{64}")
_RFC3339 = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)
_FIELDS = (
    "source",
    "source_symbol",
    "event_time",
    "available_time",
    "ingestion_time",
    "effective_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


def _canonical_source(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("FILE_PROVIDER_SOURCE_NOT_CANONICAL")
    return value


def _timestamp(value: object, *, optional: bool = False) -> datetime | None:
    if optional and value == "":
        return None
    if type(value) is not str or _RFC3339.fullmatch(value) is None:
        raise ValueError("FILE_PROVIDER_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:  # regex cannot prove calendar validity
        raise ValueError("FILE_PROVIDER_TIMESTAMP_INVALID") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("FILE_PROVIDER_TIMESTAMP_INVALID")
    return parsed.astimezone(UTC)


def _required_timestamp(value: object) -> datetime:
    parsed = _timestamp(value)
    if parsed is None:  # defensive type boundary; required input cannot be empty
        raise ValueError("FILE_PROVIDER_TIMESTAMP_INVALID")
    return parsed


@dataclass(frozen=True)
class MarketDataFileEvidence:
    """Independently supplied identity for one exact provider export."""

    source: str
    payload_sha256: str
    provider_contract_sha256: str
    observed_at: datetime
    max_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _canonical_source(self.source))
        if (
            type(self.payload_sha256) is not str
            or _DIGEST.fullmatch(self.payload_sha256) is None
            or type(self.provider_contract_sha256) is not str
            or _DIGEST.fullmatch(self.provider_contract_sha256) is None
        ):
            raise ValueError("FILE_PROVIDER_EVIDENCE_DIGEST_INVALID")
        if (
            type(self.observed_at) is not datetime
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("FILE_PROVIDER_OBSERVED_AT_INVALID")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 64 * 1024 * 1024:
            raise ValueError("FILE_PROVIDER_SIZE_BOUND_INVALID")


def market_data_file_evidence_payload(evidence: MarketDataFileEvidence) -> dict[str, object]:
    """Canonical durable identity; local path and read bound are not provenance."""
    if type(evidence) is not MarketDataFileEvidence:
        raise TypeError("FILE_PROVIDER_EVIDENCE_REQUIRED")
    return {
        "schema": "hope.market-data-file-evidence.v1",
        "source": evidence.source,
        "payload_sha256": evidence.payload_sha256,
        "provider_contract_sha256": evidence.provider_contract_sha256,
        "observed_at": evidence.observed_at.isoformat(),
    }


def parse_market_data_file_evidence(payload: object) -> MarketDataFileEvidence:
    if type(payload) is not dict or set(payload) != {
        "schema", "source", "payload_sha256", "provider_contract_sha256", "observed_at"
    }:
        raise ValueError("FILE_PROVIDER_MANIFEST_EVIDENCE_INVALID")
    values = cast(dict[str, object], payload)
    if values["schema"] != "hope.market-data-file-evidence.v1":
        raise ValueError("FILE_PROVIDER_MANIFEST_EVIDENCE_INVALID")
    observed_text = values["observed_at"]
    if type(observed_text) is not str:
        raise ValueError("FILE_PROVIDER_MANIFEST_EVIDENCE_INVALID")
    try:
        observed_at = datetime.fromisoformat(observed_text)
    except ValueError as error:
        raise ValueError("FILE_PROVIDER_MANIFEST_EVIDENCE_INVALID") from error
    if observed_at.isoformat() != observed_text:
        raise ValueError("FILE_PROVIDER_MANIFEST_EVIDENCE_INVALID")
    try:
        return MarketDataFileEvidence(
            source=cast(str, values["source"]),
            payload_sha256=cast(str, values["payload_sha256"]),
            provider_contract_sha256=cast(str, values["provider_contract_sha256"]),
            observed_at=observed_at,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("FILE_PROVIDER_MANIFEST_EVIDENCE_INVALID") from error


class VerifiedCsvMarketDataProvider:
    """Read one private regular CSV whose exact bytes are independently pinned."""

    def __init__(
        self,
        path: str | Path,
        evidence: MarketDataFileEvidence,
        *,
        request_plan: tuple[MarketDataRequest, ...] | None = None,
    ) -> None:
        if type(evidence) is not MarketDataFileEvidence:
            raise TypeError("FILE_PROVIDER_EVIDENCE_REQUIRED")
        self._path = Path(path)
        self._evidence = evidence
        self._request_plan = self._validate_plan(request_plan)

    @property
    def source(self) -> str:
        return self._evidence.source

    @property
    def evidence(self) -> MarketDataFileEvidence:
        return self._evidence

    @staticmethod
    def _request_key(request: MarketDataRequest) -> tuple[
        str, tuple[str, ...], datetime, datetime, timedelta
    ]:
        if type(request) is not MarketDataRequest:
            raise TypeError("FILE_PROVIDER_REQUEST_PLAN_INVALID")
        return (
            request.source,
            request.source_symbols,
            request.start.astimezone(UTC),
            request.end.astimezone(UTC),
            request.interval,
        )

    def _validate_plan(
        self, request_plan: tuple[MarketDataRequest, ...] | None
    ) -> tuple[MarketDataRequest, ...] | None:
        if request_plan is None:
            return None
        if type(request_plan) is not tuple or not request_plan:
            raise TypeError("FILE_PROVIDER_REQUEST_PLAN_INVALID")
        keys = tuple(self._request_key(request) for request in request_plan)
        if len(set(keys)) != len(keys):
            raise ValueError("FILE_PROVIDER_REQUEST_PLAN_DUPLICATE")
        if any(request.source != self.source for request in request_plan):
            raise ValueError("FILE_PROVIDER_REQUEST_PLAN_SOURCE_MISMATCH")
        expected_keys = [
            (request.source, symbol, event_time)
            for request in request_plan
            for symbol, event_time in request.expected_keys
        ]
        if len(expected_keys) > 1_000_000:
            raise ValueError("FILE_PROVIDER_REQUEST_PLAN_TOO_LARGE")
        if len(set(expected_keys)) != len(expected_keys):
            raise ValueError("FILE_PROVIDER_REQUEST_PLAN_OVERLAPS")
        return tuple(
            MarketDataRequest(
                source=request.source,
                source_symbols=request.source_symbols,
                start=request.start,
                end=request.end,
                interval=request.interval,
            )
            for request in request_plan
        )

    def validate_request_plan(self, requests: tuple[MarketDataRequest, ...]) -> None:
        """Fail before manifest/provider effects when lifecycle coverage is unbound."""
        if type(requests) is not tuple or not requests:
            raise TypeError("FILE_PROVIDER_REQUEST_PLAN_INVALID")
        runtime = tuple(self._request_key(request) for request in requests)
        if self._request_plan is None:
            if len(runtime) != 1:
                raise ValueError("FILE_PROVIDER_MULTI_WINDOW_PLAN_REQUIRED")
            return
        declared = tuple(self._request_key(request) for request in self._request_plan)
        if runtime != declared:
            raise ValueError("FILE_PROVIDER_RUNTIME_PLAN_MISMATCH")

    def fetch_bars(self, request: MarketDataRequest) -> ProviderMarketDataBatch:
        if type(request) is not MarketDataRequest:
            raise TypeError("FILE_PROVIDER_REQUEST_INVALID")
        if request.source != self.source:
            raise ValueError("FILE_PROVIDER_REQUEST_SOURCE_MISMATCH")
        if self._request_plan is not None and self._request_key(request) not in {
            self._request_key(planned) for planned in self._request_plan
        }:
            raise ValueError("FILE_PROVIDER_REQUEST_NOT_IN_PLAN")

        payload = bounded_regular_read(self._path, self._evidence.max_bytes)
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, self._evidence.payload_sha256):
            raise ValueError("FILE_PROVIDER_PAYLOAD_DIGEST_MISMATCH")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("FILE_PROVIDER_ENCODING_INVALID") from error
        if "\x00" in text:
            raise ValueError("FILE_PROVIDER_ENCODING_INVALID")

        reader = csv.DictReader(io.StringIO(text, newline=""))
        if tuple(reader.fieldnames or ()) != _FIELDS:
            raise ValueError("FILE_PROVIDER_SCHEMA_INVALID")
        bars: list[RawMarketBar] = []
        expected_count = (
            request.expected_count
            if self._request_plan is None
            else sum(planned.expected_count for planned in self._request_plan)
        )
        for row in reader:
            if None in row or set(row) != set(_FIELDS) or any(type(value) is not str for value in row.values()):
                raise ValueError("FILE_PROVIDER_SCHEMA_INVALID")
            values = cast(dict[str, str], row)
            if len(bars) >= expected_count:
                raise ValueError("FILE_PROVIDER_ROW_COUNT_EXCEEDED")
            bars.append(
                RawMarketBar(
                    source=_canonical_source(values["source"]),
                    source_symbol=_canonical_source(values["source_symbol"]),
                    event_time=_required_timestamp(values["event_time"]),
                    available_time=_required_timestamp(values["available_time"]),
                    ingestion_time=_required_timestamp(values["ingestion_time"]),
                    effective_time=_timestamp(values["effective_time"], optional=True),
                    open=values["open"],
                    high=values["high"],
                    low=values["low"],
                    close=values["close"],
                    volume=values["volume"],
                )
            )
        if self._request_plan is None:
            return ProviderMarketDataBatch(
                source=self.source,
                request=request,
                bars=tuple(bars),
                fetched_at=self._evidence.observed_at,
            )

        expected = {
            (planned.source, symbol, event_time)
            for planned in self._request_plan
            for symbol, event_time in planned.expected_keys
        }
        actual = {
            (bar.source, bar.source_symbol, bar.event_time.astimezone(UTC))
            for bar in bars
        }
        if len(actual) != len(bars):
            raise ValueError("FILE_PROVIDER_RETURNED_DUPLICATE_BAR_KEY")
        if actual != expected:
            raise ValueError("FILE_PROVIDER_PLANNED_COVERAGE_MISMATCH")

        selected: ProviderMarketDataBatch | None = None
        requested_key = self._request_key(request)
        for planned in self._request_plan:
            allowed = {
                (symbol, event_time) for symbol, event_time in planned.expected_keys
            }
            planned_bars = tuple(
                bar
                for bar in bars
                if (bar.source_symbol, bar.event_time.astimezone(UTC)) in allowed
            )
            batch = ProviderMarketDataBatch(
                source=self.source,
                request=planned,
                bars=planned_bars,
                fetched_at=self._evidence.observed_at,
            )
            if self._request_key(planned) == requested_key:
                selected = batch
        if selected is None:  # guarded before file read; retain local invariant
            raise ValueError("FILE_PROVIDER_REQUEST_NOT_IN_PLAN")
        return selected
