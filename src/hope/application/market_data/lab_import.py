"""Atomic LAB-only ingestion of an independently pinned provider export."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping
from uuid import UUID

from sqlalchemy import Connection, text

from hope.infrastructure.market_data.file_provider import (
    MarketDataFileEvidence,
    VerifiedCsvMarketDataProvider,
)
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from hope.infrastructure.repositories.market_data_finalizer import (
    SqlAlchemyMarketDataVersionFinalizer,
)
from hope.infrastructure.repositories.market_data_manifest import (
    SqlAlchemyMarketDataCoverageManifestRepository,
)


_LAB_DATABASE = re.compile(r"hope_[a-z0-9_]*(?:lab|test|recovery)[a-z0-9_]*")


def validate_lab_database_name(value: object) -> str:
    """Require an explicit non-production database identity."""
    if value == "hope":
        raise ValueError("MARKET_DATA_PRODUCTION_DATABASE_FORBIDDEN")
    if type(value) is not str or _LAB_DATABASE.fullmatch(value) is None:
        raise ValueError("MARKET_DATA_LAB_DATABASE_NAME_REQUIRED")
    return value


def ingest_verified_export_to_lab(
    connection: Connection,
    *,
    expected_database_name: str,
    dataset_version_id: UUID,
    universe_version_id: UUID,
    requests: tuple[MarketDataRequest, ...],
    identity_map: Mapping[tuple[str, str], UUID],
    export_path: str | Path,
    evidence: MarketDataFileEvidence,
) -> dict[str, object]:
    """Persist and seal one verified export in an existing LAB staging version.

    The caller owns the outer transaction and connection.  This function opens
    one savepoint spanning database identity, manifest declaration, provider
    validation, bar persistence and sealing, so a caught failure cannot leave a
    partial manifest or partial bar set in the caller's transaction.
    """
    if not isinstance(connection, Connection):
        raise TypeError("MARKET_DATA_LAB_CONNECTION_REQUIRED")
    expected_database_name = validate_lab_database_name(expected_database_name)
    if not isinstance(dataset_version_id, UUID):
        raise TypeError("MARKET_DATA_LAB_DATASET_VERSION_ID_REQUIRED")
    if not isinstance(universe_version_id, UUID):
        raise TypeError("MARKET_DATA_LAB_UNIVERSE_VERSION_ID_REQUIRED")
    if type(requests) is not tuple or not requests:
        raise TypeError("MARKET_DATA_LAB_REQUEST_PLAN_REQUIRED")
    if type(evidence) is not MarketDataFileEvidence:
        raise TypeError("MARKET_DATA_LAB_EVIDENCE_REQUIRED")
    if not connection.in_transaction():
        raise ValueError("MARKET_DATA_LAB_EXPLICIT_TRANSACTION_REQUIRED")

    provider = VerifiedCsvMarketDataProvider(
        export_path,
        evidence,
        request_plan=requests,
    )
    with connection.begin_nested():
        actual_database_name = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()
        if actual_database_name == "hope":
            raise ValueError("MARKET_DATA_PRODUCTION_DATABASE_FORBIDDEN")
        if actual_database_name != expected_database_name:
            raise ValueError("MARKET_DATA_LAB_DATABASE_IDENTITY_MISMATCH")

        SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(
            dataset_version_id,
            universe_version_id,
            requests,
            identity_map=identity_map,
            provider_evidence=evidence,
        )
        batches = ingest_and_seal_market_data_windows(
            provider,
            SqlAlchemyMarketBarSink(connection),
            SqlAlchemyMarketDataVersionFinalizer(connection),
            dataset_version_id,
            requests,
            identity_map=identity_map,
        )
        result = connection.execute(
            text(
                "SELECT dv.immutable, dv.vintage_label, m.manifest_hash, "
                "(SELECT count(*) FROM market_bars b "
                " WHERE b.dataset_version_id=dv.dataset_version_id) AS bar_count "
                "FROM dataset_versions dv "
                "JOIN market_data_coverage_manifests m "
                "ON m.dataset_version_id=dv.dataset_version_id "
                "WHERE dv.dataset_version_id=:dataset_version_id"
            ),
            {"dataset_version_id": dataset_version_id},
        ).mappings().one()
        expected_bar_count = sum(len(batch.bars) for batch in batches)
        if (
            result["immutable"] is not True
            or result["vintage_label"] != "sealed"
            or result["bar_count"] != expected_bar_count
        ):
            raise ValueError("MARKET_DATA_LAB_POSTCONDITION_FAILED")
        return {
            "schema": "hope.lab-market-data-import.v1",
            "database_name": actual_database_name,
            "dataset_version_id": str(dataset_version_id),
            "universe_version_id": str(universe_version_id),
            "manifest_sha256": result["manifest_hash"],
            "bar_count": result["bar_count"],
            "sealed": True,
            "paper_authority": False,
            "release_admission": False,
            "live_enabled": False,
        }
