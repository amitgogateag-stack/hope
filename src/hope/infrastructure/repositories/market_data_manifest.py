from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Mapping
from uuid import UUID

from sqlalchemy import Connection, text

from hope.infrastructure.market_data.provider import MarketDataRequest


class SqlAlchemyMarketDataCoverageManifestRepository:
    """Declare one immutable intended-coverage manifest per empty staging version."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def declare(
        self,
        dataset_version_id: UUID,
        universe_version_id: UUID,
        requests: tuple[MarketDataRequest, ...],
        *,
        identity_map: Mapping[tuple[str, str], UUID],
    ) -> None:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("MARKET_DATA_MANIFEST_REQUIRES_DATASET_VERSION_ID")
        if not isinstance(universe_version_id, UUID):
            raise TypeError("MARKET_DATA_MANIFEST_REQUIRES_UNIVERSE_VERSION_ID")

        payload = build_market_data_manifest(
            universe_version_id,
            requests,
            identity_map=identity_map,
        )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        manifest_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        with self._connection.begin_nested():
            version = self._connection.execute(
                text(
                    "SELECT immutable FROM dataset_versions "
                    "WHERE dataset_version_id = :version_id FOR UPDATE"
                ),
                {"version_id": dataset_version_id},
            ).mappings().one_or_none()
            if version is None:
                raise ValueError("MARKET_DATA_MANIFEST_DATASET_VERSION_NOT_FOUND")
            if version["immutable"]:
                raise ValueError("MARKET_DATA_MANIFEST_DATASET_VERSION_IMMUTABLE")

            universe = self._connection.execute(
                text(
                    "SELECT pit_certified, declared_member_count FROM universe_versions "
                    "WHERE universe_version_id = :universe_version_id FOR SHARE"
                ),
                {"universe_version_id": universe_version_id},
            ).mappings().one_or_none()
            if universe is None:
                raise ValueError("MARKET_DATA_MANIFEST_UNIVERSE_VERSION_NOT_FOUND")
            if not universe["pit_certified"]:
                raise ValueError("MARKET_DATA_MANIFEST_UNIVERSE_NOT_PIT_CERTIFIED")

            member_rows = self._connection.execute(
                text(
                    "SELECT instrument_id, valid_from, valid_to FROM universe_members "
                    "WHERE universe_version_id = :universe_version_id"
                ),
                {"universe_version_id": universe_version_id},
            ).mappings().all()
            if len(member_rows) != universe["declared_member_count"]:
                raise ValueError("MARKET_DATA_MANIFEST_UNIVERSE_CARDINALITY_MISMATCH")

            memberships = {
                row["instrument_id"]: (row["valid_from"], row["valid_to"])
                for row in member_rows
            }
            _validate_requested_membership(
                requests,
                identity_map=identity_map,
                memberships=memberships,
            )

            existing = self._connection.execute(
                text(
                    "SELECT manifest_hash, universe_version_id "
                    "FROM market_data_coverage_manifests "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).mappings().one_or_none()
            if existing is not None:
                if (
                    existing["manifest_hash"] != manifest_hash
                    or existing["universe_version_id"] != universe_version_id
                ):
                    raise ValueError("MARKET_DATA_MANIFEST_CONFLICT")
                return

            bar_count = self._connection.execute(
                text(
                    "SELECT count(*) FROM market_bars "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).scalar_one()
            if bar_count:
                raise ValueError("MARKET_DATA_MANIFEST_REQUIRES_EMPTY_STAGING_VERSION")

            self._connection.execute(
                text(
                    "INSERT INTO market_data_coverage_manifests("
                    "dataset_version_id, universe_version_id, manifest_hash, manifest"
                    ") VALUES ("
                    ":version_id, :universe_version_id, :manifest_hash, CAST(:manifest AS JSONB)"
                    ")"
                ),
                {
                    "version_id": dataset_version_id,
                    "universe_version_id": universe_version_id,
                    "manifest_hash": manifest_hash,
                    "manifest": canonical,
                },
            )


def build_market_data_manifest(
    universe_version_id: UUID,
    requests: tuple[MarketDataRequest, ...],
    *,
    identity_map: Mapping[tuple[str, str], UUID],
) -> dict[str, object]:
    if not isinstance(universe_version_id, UUID):
        raise TypeError("MARKET_DATA_MANIFEST_REQUIRES_UNIVERSE_VERSION_ID")
    if not requests:
        raise ValueError("MARKET_DATA_MANIFEST_WINDOWS_REQUIRED")

    windows: list[dict[str, object]] = []
    declared_keys: set[tuple[UUID, datetime]] = set()
    for request in requests:
        interval_seconds = request.interval.total_seconds()
        if not interval_seconds.is_integer():
            raise ValueError("MARKET_DATA_MANIFEST_INTERVAL_REQUIRES_WHOLE_SECONDS")

        instruments: list[dict[str, str]] = []
        for symbol in request.source_symbols:
            key = (request.source, symbol)
            if key not in identity_map:
                raise ValueError("MARKET_DATA_MANIFEST_IDENTITY_MAP_INCOMPLETE")
            instrument_id = identity_map[key]
            if not isinstance(instrument_id, UUID):
                raise TypeError("MARKET_DATA_MANIFEST_IDENTITY_MAP_INVALID")
            instruments.append(
                {
                    "source_symbol": symbol,
                    "instrument_id": str(instrument_id),
                }
            )
        request_keys = tuple(
            (identity_map[(request.source, source_symbol)], event_time)
            for source_symbol, event_time in request.expected_keys
        )
        if (
            len(set(request_keys)) != len(request_keys)
            or declared_keys.intersection(request_keys)
        ):
            raise ValueError("MARKET_DATA_MANIFEST_DUPLICATE_LOGICAL_KEY")
        declared_keys.update(request_keys)
        windows.append(
            {
                "source": request.source,
                "start": request.start.isoformat(),
                "end": request.end.isoformat(),
                "interval_seconds": int(interval_seconds),
                "instruments": instruments,
            }
        )
    return {
        "version": 2,
        "universe_version_id": str(universe_version_id),
        "windows": windows,
    }


def manifest_universe_version_id(manifest: Mapping[str, object]) -> UUID:
    if manifest.get("version") != 2:
        raise ValueError("MARKET_DATA_MANIFEST_VERSION_UNSUPPORTED")
    try:
        universe_version_id_text = manifest["universe_version_id"]
    except KeyError as exc:
        raise ValueError("MARKET_DATA_MANIFEST_INVALID") from exc
    if not isinstance(universe_version_id_text, str):
        raise ValueError("MARKET_DATA_MANIFEST_INVALID")
    try:
        universe_version_id = UUID(universe_version_id_text)
    except ValueError as exc:
        raise ValueError("MARKET_DATA_MANIFEST_INVALID") from exc
    if str(universe_version_id) != universe_version_id_text:
        raise ValueError("MARKET_DATA_MANIFEST_INVALID")
    return universe_version_id


def manifest_expected_keys(manifest: Mapping[str, object]) -> set[tuple[UUID, datetime]]:
    return manifest_evidence(manifest)[0]


def manifest_evidence(
    manifest: Mapping[str, object],
) -> tuple[set[tuple[UUID, datetime]], dict[tuple[str, str], UUID]]:
    manifest_universe_version_id(manifest)
    raw_windows = manifest.get("windows")
    if not isinstance(raw_windows, list) or not raw_windows:
        raise ValueError("MARKET_DATA_MANIFEST_INVALID")

    expected: set[tuple[UUID, datetime]] = set()
    identity_bindings: dict[tuple[str, str], UUID] = {}
    for raw_window in raw_windows:
        if not isinstance(raw_window, dict):
            raise ValueError("MARKET_DATA_MANIFEST_INVALID")
        try:
            source = raw_window["source"]
            start_text = raw_window["start"]
            end_text = raw_window["end"]
            interval_seconds = raw_window["interval_seconds"]
            instruments = raw_window["instruments"]
        except KeyError as exc:
            raise ValueError("MARKET_DATA_MANIFEST_INVALID") from exc
        if (
            not isinstance(start_text, str)
            or not isinstance(end_text, str)
            or not isinstance(interval_seconds, int)
            or isinstance(interval_seconds, bool)
        ):
            raise ValueError("MARKET_DATA_MANIFEST_INVALID")
        try:
            start = datetime.fromisoformat(start_text)
            end = datetime.fromisoformat(end_text)
            interval = timedelta(seconds=interval_seconds)
        except (ValueError, OverflowError) as exc:
            raise ValueError("MARKET_DATA_MANIFEST_INVALID") from exc
        if (
            not isinstance(source, str)
            or not source
            or source != source.strip()
            or start.isoformat() != start_text
            or end.isoformat() != end_text
            or start.tzinfo is None
            or start.utcoffset() is None
            or end.tzinfo is None
            or end.utcoffset() is None
            or end <= start
            or interval <= timedelta(0)
            or (end - start) % interval != timedelta(0)
        ):
            raise ValueError("MARKET_DATA_MANIFEST_INVALID")
        if not isinstance(instruments, list) or not instruments:
            raise ValueError("MARKET_DATA_MANIFEST_INVALID")

        instrument_ids: list[UUID] = []
        source_symbols: list[str] = []
        for instrument in instruments:
            if not isinstance(instrument, dict):
                raise ValueError("MARKET_DATA_MANIFEST_INVALID")
            try:
                source_symbol = instrument["source_symbol"]
                instrument_id_text = instrument["instrument_id"]
                instrument_id = UUID(str(instrument_id_text))
            except (KeyError, ValueError) as exc:
                raise ValueError("MARKET_DATA_MANIFEST_INVALID") from exc
            if (
                not isinstance(source_symbol, str)
                or not source_symbol
                or source_symbol != source_symbol.strip()
                or not isinstance(instrument_id_text, str)
                or str(instrument_id) != instrument_id_text
            ):
                raise ValueError("MARKET_DATA_MANIFEST_INVALID")
            identity_key = (source, source_symbol)
            existing_instrument_id = identity_bindings.get(identity_key)
            if (
                existing_instrument_id is not None
                and existing_instrument_id != instrument_id
            ):
                raise ValueError("MARKET_DATA_MANIFEST_IDENTITY_BINDING_CONFLICT")
            identity_bindings[identity_key] = instrument_id
            source_symbols.append(source_symbol)
            instrument_ids.append(instrument_id)
        if (
            len(set(source_symbols)) != len(source_symbols)
            or len(set(instrument_ids)) != len(instrument_ids)
        ):
            raise ValueError("MARKET_DATA_MANIFEST_INVALID")

        cursor = start
        while cursor < end:
            window_keys = {(instrument_id, cursor) for instrument_id in instrument_ids}
            if expected.intersection(window_keys):
                raise ValueError("MARKET_DATA_MANIFEST_DUPLICATE_LOGICAL_KEY")
            expected.update(window_keys)
            cursor += interval
    return expected, identity_bindings


def manifest_expected_keys_for_requests(
    manifest: Mapping[str, object],
    requests: tuple[MarketDataRequest, ...],
    *,
    identity_map: Mapping[tuple[str, str], UUID],
) -> set[tuple[UUID, datetime]]:
    expected, declared_identity_bindings = manifest_evidence(manifest)
    for request in requests:
        for source_symbol in request.source_symbols:
            identity_key = (request.source, source_symbol)
            if identity_key not in identity_map:
                raise ValueError("MARKET_DATA_FINALIZE_IDENTITY_MAP_INCOMPLETE")
            instrument_id = identity_map[identity_key]
            if declared_identity_bindings.get(identity_key) != instrument_id:
                raise ValueError("MARKET_DATA_REQUEST_IDENTITY_BINDING_MISMATCH")
    return expected


def _validate_requested_membership(
    requests: tuple[MarketDataRequest, ...],
    *,
    identity_map: Mapping[tuple[str, str], UUID],
    memberships: Mapping[UUID, tuple[datetime | None, datetime | None]],
) -> None:
    for request in requests:
        for source_symbol, event_time in request.expected_keys:
            instrument_id = identity_map[(request.source, source_symbol)]
            interval = memberships.get(instrument_id)
            if interval is None:
                raise ValueError("MARKET_DATA_MANIFEST_INSTRUMENT_NOT_IN_UNIVERSE")
            valid_from, valid_to = interval
            if valid_from is not None and event_time < valid_from:
                raise ValueError("MARKET_DATA_MANIFEST_REQUEST_OUTSIDE_UNIVERSE_MEMBERSHIP")
            if valid_to is not None and event_time >= valid_to:
                raise ValueError("MARKET_DATA_MANIFEST_REQUEST_OUTSIDE_UNIVERSE_MEMBERSHIP")
