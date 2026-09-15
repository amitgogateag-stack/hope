from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from hope.domain.market_data.context import PITMarketContext
from hope.domain.market_data.models import MarketBar
from hope.infrastructure.repositories.market_data_manifest import (
    manifest_evidence,
    manifest_universe_version_id,
    validate_manifest_membership,
)


def _verify_read_side_evidence(
    dataset: Mapping[str, object],
) -> set[tuple[UUID, datetime]]:
    manifest = dataset.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("PIT_MARKET_CONTEXT_REQUIRES_MANIFEST_BACKED_SEALED_VERSION")

    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if dataset.get("manifest_hash") != actual_hash:
        raise ValueError("PIT_MARKET_CONTEXT_MANIFEST_CHECKSUM_MISMATCH")

    manifest_universe_id = manifest_universe_version_id(manifest)
    if dataset.get("universe_version_id") != manifest_universe_id:
        raise ValueError("PIT_MARKET_CONTEXT_MANIFEST_UNIVERSE_MISMATCH")
    if dataset.get("universe_pit_certified") is not True:
        raise ValueError("PIT_MARKET_CONTEXT_REQUIRES_PIT_CERTIFIED_UNIVERSE")
    if dataset.get("actual_member_count") != dataset.get("declared_member_count"):
        raise ValueError("PIT_MARKET_CONTEXT_UNIVERSE_CARDINALITY_MISMATCH")

    expected_keys, _ = manifest_evidence(
        manifest,
        expected_source=dataset.get("source"),
    )
    if not expected_keys:
        raise ValueError("PIT_MARKET_CONTEXT_MANIFEST_EVIDENCE_EMPTY")
    return expected_keys


def _verify_read_side_membership(
    expected_keys: set[tuple[UUID, datetime]],
    memberships: Mapping[UUID, tuple[datetime | None, datetime | None]],
) -> None:
    try:
        validate_manifest_membership(expected_keys, memberships=memberships)
    except ValueError as exc:
        raise ValueError("PIT_MARKET_CONTEXT_UNIVERSE_MEMBERSHIP_MISMATCH") from exc


def _verify_requested_instruments(
    expected_keys: set[tuple[UUID, datetime]],
    requested_instrument_ids: tuple[UUID, ...],
) -> None:
    declared_instrument_ids = {instrument_id for instrument_id, _ in expected_keys}
    if not set(requested_instrument_ids).issubset(declared_instrument_ids):
        raise ValueError("PIT_MARKET_CONTEXT_INSTRUMENT_OUTSIDE_MANIFEST")


class PITMarketContextRepository:
    """Authoritative PIT context loader from verified manifest-backed evidence."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(
        self,
        dataset_version_id: UUID,
        *,
        as_of: datetime,
        instrument_ids: tuple[UUID, ...],
    ) -> PITMarketContext:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("PIT_MARKET_CONTEXT_REPOSITORY_REQUIRES_DATASET_VERSION_ID")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("PIT_MARKET_CONTEXT_REPOSITORY_REQUIRES_AWARE_AS_OF")
        if not isinstance(instrument_ids, tuple) or any(
            not isinstance(instrument_id, UUID) for instrument_id in instrument_ids
        ):
            raise TypeError("PIT_MARKET_CONTEXT_REPOSITORY_REQUIRES_INSTRUMENT_IDS")

        dataset = self._connection.execute(
            text(
                "SELECT dv.immutable, dv.vintage_label, d.pit_certified, d.source, "
                "m.manifest, m.manifest_hash, m.universe_version_id, "
                "uv.pit_certified AS universe_pit_certified, uv.declared_member_count, "
                "(SELECT count(*) FROM universe_members um "
                "WHERE um.universe_version_id = m.universe_version_id) AS actual_member_count "
                "FROM dataset_versions dv "
                "JOIN datasets d ON d.dataset_id = dv.dataset_id "
                "LEFT JOIN market_data_coverage_manifests m "
                "ON m.dataset_version_id = dv.dataset_version_id "
                "LEFT JOIN universe_versions uv "
                "ON uv.universe_version_id = m.universe_version_id "
                "WHERE dv.dataset_version_id = :dataset_version_id"
            ),
            {"dataset_version_id": dataset_version_id},
        ).mappings().first()
        if dataset is None:
            raise RuntimeError("PIT_MARKET_CONTEXT_DATASET_VERSION_NOT_FOUND")
        if not dataset["immutable"]:
            raise ValueError("PIT_MARKET_CONTEXT_REQUIRES_IMMUTABLE_DATASET_VERSION")
        if not dataset["pit_certified"]:
            raise ValueError("PIT_MARKET_CONTEXT_REQUIRES_PIT_CERTIFIED_DATASET")
        if dataset["vintage_label"] != "sealed" or dataset["manifest"] is None:
            raise ValueError("PIT_MARKET_CONTEXT_REQUIRES_MANIFEST_BACKED_SEALED_VERSION")

        expected_keys = _verify_read_side_evidence(dataset)
        member_rows = self._connection.execute(
            text(
                "SELECT instrument_id, valid_from, valid_to FROM universe_members "
                "WHERE universe_version_id = :universe_version_id"
            ),
            {"universe_version_id": dataset["universe_version_id"]},
        ).mappings()
        _verify_read_side_membership(
            expected_keys,
            {
                row["instrument_id"]: (row["valid_from"], row["valid_to"])
                for row in member_rows
            },
        )
        _verify_requested_instruments(expected_keys, instrument_ids)

        if not instrument_ids:
            return PITMarketContext(as_of=as_of, bars=())

        statement = text(
            "SELECT instrument_id, event_time, available_time, effective_time, ingestion_time, "
            "open, high, low, close, volume "
            "FROM market_bars "
            "WHERE dataset_version_id = :dataset_version_id "
            "AND instrument_id IN :instrument_ids "
            "AND event_time <= :as_of "
            "AND available_time <= :as_of "
            "AND ingestion_time <= :as_of "
            "ORDER BY event_time, available_time, ingestion_time, instrument_id"
        ).bindparams(bindparam("instrument_ids", expanding=True))
        rows = self._connection.execute(
            statement,
            {
                "dataset_version_id": dataset_version_id,
                "instrument_ids": instrument_ids,
                "as_of": as_of,
            },
        ).mappings()
        bars = tuple(
            MarketBar(
                instrument_id=str(row["instrument_id"]),
                event_time=row["event_time"],
                available_time=row["available_time"],
                effective_time=row["effective_time"],
                ingestion_time=row["ingestion_time"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in rows
        )
        return PITMarketContext(as_of=as_of, bars=bars)
