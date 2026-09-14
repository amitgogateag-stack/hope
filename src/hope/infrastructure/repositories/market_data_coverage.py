from __future__ import annotations

from datetime import datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy import Connection, text

from hope.infrastructure.market_data.provider import MarketDataRequest


class SqlAlchemyMarketDataCoverageVerifier:
    """Prove the persisted logical market-bar grid exactly matches requests."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def verify(
        self,
        dataset_version_id: UUID,
        requests: tuple[MarketDataRequest, ...],
        *,
        identity_map: Mapping[tuple[str, str], UUID],
    ) -> None:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("MARKET_DATA_COVERAGE_REQUIRES_DATASET_VERSION_ID")
        if not requests:
            raise ValueError("MARKET_DATA_COVERAGE_WINDOWS_REQUIRED")

        expected: set[tuple[UUID, datetime]] = set()
        for request in requests:
            for source_symbol, event_time in request.expected_keys:
                identity_key = (request.source, source_symbol)
                if identity_key not in identity_map:
                    raise ValueError("MARKET_DATA_COVERAGE_IDENTITY_MAP_INCOMPLETE")
                instrument_id = identity_map[identity_key]
                if not isinstance(instrument_id, UUID):
                    raise TypeError("MARKET_DATA_COVERAGE_IDENTITY_MAP_INVALID")
                expected.add((instrument_id, event_time))

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
