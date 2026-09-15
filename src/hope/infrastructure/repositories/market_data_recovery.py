from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy import Connection, text

from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.repositories.market_data_manifest import (
    manifest_evidence,
    manifest_universe_version_id,
    validate_manifest_membership,
    validate_manifest_request_subset,
)


class SqlAlchemyMarketDataRecoveryAuthorizer:
    """Authorize restart/retry subsets against durable market-data intent."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def preflight_subset(
        self,
        dataset_version_id: UUID,
        requests: tuple[MarketDataRequest, ...],
        *,
        identity_map: Mapping[tuple[str, str], UUID],
    ) -> None:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("MARKET_DATA_RECOVERY_PREFLIGHT_REQUIRES_DATASET_VERSION_ID")
        if not isinstance(requests, tuple):
            raise TypeError("MARKET_DATA_RECOVERY_PREFLIGHT_WINDOWS_REQUIRE_TUPLE")
        if not requests:
            raise ValueError("MARKET_DATA_RECOVERY_PREFLIGHT_WINDOWS_REQUIRED")

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
                raise ValueError("MARKET_DATA_RECOVERY_DATASET_VERSION_NOT_FOUND")
            if version["immutable"] or version["vintage_label"] != "staging":
                raise ValueError("MARKET_DATA_RECOVERY_REQUIRES_STAGING_VERSION")
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
            actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if actual_hash != manifest_row["manifest_hash"]:
                raise ValueError("MARKET_DATA_COVERAGE_MANIFEST_HASH_MISMATCH")

            universe_version_id = manifest_universe_version_id(manifest)
            if manifest_row["universe_version_id"] != universe_version_id:
                raise ValueError("MARKET_DATA_COVERAGE_MANIFEST_UNIVERSE_MISMATCH")

            expected, declared_bindings = manifest_evidence(
                manifest, expected_source=version["source"]
            )
            requested: set[tuple[UUID, datetime]] = set()
            for request in requests:
                if request.source != version["source"]:
                    raise ValueError("MARKET_DATA_MANIFEST_DATASET_SOURCE_MISMATCH")
                for source_symbol, event_time in request.expected_keys:
                    identity_key = (request.source, source_symbol)
                    if identity_key not in identity_map:
                        raise ValueError("MARKET_DATA_RECOVERY_IDENTITY_MAP_INCOMPLETE")
                    instrument_id = identity_map[identity_key]
                    if not isinstance(instrument_id, UUID):
                        raise TypeError("MARKET_DATA_RECOVERY_IDENTITY_MAP_INVALID")
                    if declared_bindings.get(identity_key) != instrument_id:
                        raise ValueError("MARKET_DATA_REQUEST_IDENTITY_BINDING_MISMATCH")
                    requested.add((instrument_id, event_time))
            if not requested.issubset(expected):
                raise ValueError("MARKET_DATA_RECOVERY_REQUEST_OUTSIDE_DECLARED_MANIFEST")
            validate_manifest_request_subset(
                manifest,
                requests,
                identity_map=identity_map,
            )

            universe = self._connection.execute(
                text(
                    "SELECT pit_certified, declared_member_count FROM universe_versions "
                    "WHERE universe_version_id = :universe_version_id"
                ),
                {"universe_version_id": universe_version_id},
            ).mappings().one_or_none()
            if universe is None or not universe["pit_certified"]:
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
            validate_manifest_membership(
                requested,
                memberships={
                    row["instrument_id"]: (row["valid_from"], row["valid_to"])
                    for row in member_rows
                },
            )
