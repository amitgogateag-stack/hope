from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy import Connection, text

from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.repositories.market_data_manifest import (
    build_market_data_manifest,
    manifest_expected_keys_for_requests,
    manifest_universe_version_id,
)


class SqlAlchemyMarketDataVersionFinalizer:
    """Preflight intended coverage, then atomically prove coverage and seal."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def preflight(
        self,
        dataset_version_id: UUID,
        requests: tuple[MarketDataRequest, ...],
        *,
        identity_map: Mapping[tuple[str, str], UUID],
    ) -> None:
        """Prove the runtime plan exactly matches durable intent before fetching."""
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("MARKET_DATA_PREFLIGHT_REQUIRES_DATASET_VERSION_ID")
        if not isinstance(requests, tuple):
            raise TypeError("MARKET_DATA_PREFLIGHT_WINDOWS_REQUIRE_TUPLE")
        if not requests:
            raise ValueError("MARKET_DATA_PREFLIGHT_WINDOWS_REQUIRED")

        with self._connection.begin_nested():
            version = self._connection.execute(
                text(
                    "SELECT dv.immutable, dv.vintage_label, d.pit_certified, d.source "
                    "FROM dataset_versions dv JOIN datasets d ON d.dataset_id = dv.dataset_id "
                    "WHERE dv.dataset_version_id = :version_id FOR SHARE"
                ),
                {"version_id": dataset_version_id},
            ).mappings().one_or_none()
            if version is None:
                raise ValueError("MARKET_DATA_DATASET_VERSION_NOT_FOUND")
            if version["immutable"] or version["vintage_label"] != "staging":
                raise ValueError("MARKET_DATA_PREFLIGHT_REQUIRES_STAGING_VERSION")
            if not version["pit_certified"]:
                raise ValueError("MARKET_DATA_DATASET_NOT_PIT_CERTIFIED")

            manifest_row = self._connection.execute(
                text(
                    "SELECT universe_version_id, manifest_hash, manifest "
                    "FROM market_data_coverage_manifests "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).mappings().one_or_none()
            if manifest_row is None:
                raise ValueError("MARKET_DATA_COVERAGE_MANIFEST_REQUIRED")

            manifest = manifest_row["manifest"]
            canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != manifest_row["manifest_hash"]:
                raise ValueError("MARKET_DATA_COVERAGE_MANIFEST_HASH_MISMATCH")

            universe_version_id = manifest_universe_version_id(manifest)
            if manifest_row["universe_version_id"] != universe_version_id:
                raise ValueError("MARKET_DATA_COVERAGE_MANIFEST_UNIVERSE_MISMATCH")

            runtime_manifest = build_market_data_manifest(
                universe_version_id,
                requests,
                identity_map=identity_map,
            )
            runtime_canonical = json.dumps(
                runtime_manifest, sort_keys=True, separators=(",", ":")
            )
            runtime_hash = hashlib.sha256(runtime_canonical.encode("utf-8")).hexdigest()
            if runtime_hash != manifest_row["manifest_hash"]:
                raise ValueError("MARKET_DATA_RUNTIME_PLAN_MANIFEST_MISMATCH")

            if any(request.source != version["source"] for request in requests):
                raise ValueError("MARKET_DATA_MANIFEST_DATASET_SOURCE_MISMATCH")

            universe = self._connection.execute(
                text(
                    "SELECT pit_certified, declared_member_count FROM universe_versions "
                    "WHERE universe_version_id = :universe_version_id"
                ),
                {"universe_version_id": universe_version_id},
            ).mappings().one_or_none()
            if universe is None or not universe["pit_certified"]:
                raise ValueError("MARKET_DATA_MANIFEST_UNIVERSE_NOT_PIT_CERTIFIED")
            actual_member_count = self._connection.execute(
                text(
                    "SELECT count(*) FROM universe_members "
                    "WHERE universe_version_id = :universe_version_id"
                ),
                {"universe_version_id": universe_version_id},
            ).scalar_one()
            if actual_member_count != universe["declared_member_count"]:
                raise ValueError("MARKET_DATA_MANIFEST_UNIVERSE_CARDINALITY_MISMATCH")

    def finalize(
        self,
        dataset_version_id: UUID,
        requests: tuple[MarketDataRequest, ...],
        *,
        identity_map: Mapping[tuple[str, str], UUID],
    ) -> None:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("MARKET_DATA_FINALIZE_REQUIRES_DATASET_VERSION_ID")
        if not requests:
            raise ValueError("MARKET_DATA_FINALIZE_WINDOWS_REQUIRED")

        requested: set[tuple[UUID, datetime]] = set()
        for request in requests:
            for source_symbol, event_time in request.expected_keys:
                identity_key = (request.source, source_symbol)
                if identity_key not in identity_map:
                    raise ValueError("MARKET_DATA_FINALIZE_IDENTITY_MAP_INCOMPLETE")
                instrument_id = identity_map[identity_key]
                if not isinstance(instrument_id, UUID):
                    raise TypeError("MARKET_DATA_FINALIZE_IDENTITY_MAP_INVALID")
                requested.add((instrument_id, event_time))

        with self._connection.begin_nested():
            version = self._connection.execute(
                text(
                    "SELECT dv.immutable, dv.vintage_label, d.pit_certified, d.source "
                    "FROM dataset_versions dv JOIN datasets d ON d.dataset_id = dv.dataset_id "
                    "WHERE dv.dataset_version_id = :version_id FOR UPDATE"
                ),
                {"version_id": dataset_version_id},
            ).mappings().one_or_none()
            if version is None:
                raise ValueError("MARKET_DATA_DATASET_VERSION_NOT_FOUND")
            if version["immutable"] and version["vintage_label"] != "sealed":
                raise ValueError("MARKET_DATA_DATASET_VERSION_ALREADY_IMMUTABLE")
            if not version["pit_certified"]:
                raise ValueError("MARKET_DATA_DATASET_NOT_PIT_CERTIFIED")
            if not version["immutable"] and version["vintage_label"] != "staging":
                raise ValueError("MARKET_DATA_FINALIZE_REQUIRES_STAGING_VERSION")

            manifest_row = self._connection.execute(
                text(
                    "SELECT universe_version_id, manifest_hash, manifest "
                    "FROM market_data_coverage_manifests "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).mappings().one_or_none()
            if manifest_row is None:
                raise ValueError("MARKET_DATA_COVERAGE_MANIFEST_REQUIRED")

            manifest = manifest_row["manifest"]
            canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
            actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if actual_hash != manifest_row["manifest_hash"]:
                raise ValueError("MARKET_DATA_COVERAGE_MANIFEST_HASH_MISMATCH")

            manifest_universe_id = manifest_universe_version_id(manifest)
            if manifest_row["universe_version_id"] != manifest_universe_id:
                raise ValueError("MARKET_DATA_COVERAGE_MANIFEST_UNIVERSE_MISMATCH")

            universe = self._connection.execute(
                text(
                    "SELECT pit_certified, declared_member_count FROM universe_versions "
                    "WHERE universe_version_id = :universe_version_id"
                ),
                {"universe_version_id": manifest_universe_id},
            ).mappings().one_or_none()
            if universe is None or not universe["pit_certified"]:
                raise ValueError("MARKET_DATA_MANIFEST_UNIVERSE_NOT_PIT_CERTIFIED")
            actual_member_count = self._connection.execute(
                text(
                    "SELECT count(*) FROM universe_members "
                    "WHERE universe_version_id = :universe_version_id"
                ),
                {"universe_version_id": manifest_universe_id},
            ).scalar_one()
            if actual_member_count != universe["declared_member_count"]:
                raise ValueError("MARKET_DATA_MANIFEST_UNIVERSE_CARDINALITY_MISMATCH")

            expected = manifest_expected_keys_for_requests(
                manifest,
                requests,
                identity_map=identity_map,
                expected_source=version["source"],
            )
            if not requested.issubset(expected):
                raise ValueError("MARKET_DATA_REQUEST_OUTSIDE_DECLARED_MANIFEST")

            rows = self._connection.execute(
                text(
                    "SELECT instrument_id, event_time FROM market_bars "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).all()
            actual = tuple((row[0], row[1]) for row in rows)
            actual_set = set(actual)
            if len(actual_set) != len(actual):
                raise ValueError("MARKET_DATA_PERSISTED_COVERAGE_DUPLICATE_LOGICAL_KEY")
            if expected - actual_set:
                raise ValueError("MARKET_DATA_PERSISTED_COVERAGE_MISSING")
            if actual_set - expected:
                raise ValueError("MARKET_DATA_PERSISTED_COVERAGE_EXTRA")
            if not actual:
                raise ValueError("MARKET_DATA_DATASET_VERSION_EMPTY")
            if version["immutable"]:
                return
            self._connection.execute(
                text(
                    "UPDATE dataset_versions SET immutable = TRUE, vintage_label = 'sealed' "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            )
