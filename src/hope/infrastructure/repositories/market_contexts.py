from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from hope.domain.market_data.context import PITMarketContext
from hope.domain.market_data.models import MarketBar


class PITMarketContextRepository:
    """Authoritative PIT context loader from one immutable, PIT-certified dataset version."""

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
                "SELECT dv.immutable, d.pit_certified "
                "FROM dataset_versions dv "
                "JOIN datasets d ON d.dataset_id = dv.dataset_id "
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
