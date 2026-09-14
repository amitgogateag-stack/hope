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
        return UUID(str(manifest["universe_version_id"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("MARKET_DATA_MANIFEST_INVALID") from exc


def manifest_expected_keys(manifest: Mapping[str, object]) -> set[tuple[UUID, datetime]]:
    manifest_universe_version_id(manifest)
    raw_windows = manifest.get("windows")
    if not isinstance(raw_windows, list) or not raw_windows:
        raise ValueError("MARKET_DATA_MANIFEST_INVALID")

    expected: set[tuple[UUID, datetime]] = set()
    for raw_window in raw_windows:
        if not isinstance(raw_window, dict):
            raise ValueError("MARKET_DATA_MANIFEST_INVALID")
        try:
            start = datetime.fromisoformat(str(raw_window["start"]))
            end = datetime.fromisoformat(str(raw_window["end"]))
            interval = timedelta(seconds=int(raw_window["interval_seconds"]))
            instruments = raw_window["instruments"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("MARKET_DATA_MANIFEST_INVALID") from exc
        if start.tzinfo is None or end.tzinfo is None or end <= start or interval <= timedelta(0):
            raise ValueError("MARKET_DATA_MANIFEST_INVALID")
        if not isinstance(instruments, list) or not instruments:
            raise ValueError("MARKET_DATA_MANIFEST_INVALID")

        instrument_ids: list[UUID] = []
        for instrument in instruments:
            if not isinstance(instrument, dict):
                raise ValueError("MARKET_DATA_MANIFEST_INVALID")
            try:
                instrument_ids.append(UUID(str(instrument["instrument_id"])))
            except (KeyError, ValueError) as exc:
                raise ValueError("MARKET_DATA_MANIFEST_INVALID") from exc

        cursor = start
        while cursor < end:
            expected.update((instrument_id, cursor) for instrument_id in instrument_ids)
            cursor += interval
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
