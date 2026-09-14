from __future__ import annotations

from datetime import datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy import Connection, text

from hope.infrastructure.market_data.provider import MarketDataRequest


class SqlAlchemyMarketDataVersionFinalizer:
    """Atomically prove persisted coverage and seal one market-data version.

    The dataset-version row is locked before coverage is read and remains locked
    through the immutable transition. This closes the race where another writer
    could append staging data after coverage verification but before sealing.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

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

        expected: set[tuple[UUID, datetime]] = set()
        for request in requests:
            for source_symbol, event_time in request.expected_keys:
                identity_key = (request.source, source_symbol)
                if identity_key not in identity_map:
                    raise ValueError("MARKET_DATA_FINALIZE_IDENTITY_MAP_INCOMPLETE")
                instrument_id = identity_map[identity_key]
                if not isinstance(instrument_id, UUID):
                    raise TypeError("MARKET_DATA_FINALIZE_IDENTITY_MAP_INVALID")
                expected.add((instrument_id, event_time))

        with self._connection.begin_nested():
            version = self._connection.execute(
                text(
                    "SELECT dv.immutable, dv.vintage_label, d.pit_certified "
                    "FROM dataset_versions dv "
                    "JOIN datasets d ON d.dataset_id = dv.dataset_id "
                    "WHERE dv.dataset_version_id = :version_id "
                    "FOR UPDATE"
                ),
                {"version_id": dataset_version_id},
            ).mappings().one_or_none()

            if version is None:
                raise ValueError("MARKET_DATA_DATASET_VERSION_NOT_FOUND")
            if version["immutable"] and version["vintage_label"] != "sealed":
                raise ValueError("MARKET_DATA_DATASET_VERSION_ALREADY_IMMUTABLE")
            if not version["pit_certified"]:
                raise ValueError("MARKET_DATA_DATASET_NOT_PIT_CERTIFIED")

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
                    "UPDATE dataset_versions "
                    "SET immutable = TRUE, vintage_label = 'sealed' "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            )
