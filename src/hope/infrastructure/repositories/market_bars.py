from __future__ import annotations

from uuid import UUID

from sqlalchemy import Column, Connection, DateTime, MetaData, Numeric, Table, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hope.domain.market_data.models import MarketBar


class SqlAlchemyMarketBarSink:
    """Append canonical market bars with exact-retry idempotency.

    Database immutability triggers remain authoritative for sealed dataset
    versions. A collision on the PIT identity key is accepted only when the
    stored payload is exactly the same evidence; conflicting evidence fails
    closed.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        metadata = MetaData()
        self._market_bars = Table(
            "market_bars",
            metadata,
            Column("market_bar_id", Uuid, primary_key=True),
            Column("dataset_version_id", Uuid, nullable=False),
            Column("instrument_id", Uuid, nullable=False),
            Column("event_time", DateTime(timezone=True), nullable=False),
            Column("available_time", DateTime(timezone=True), nullable=False),
            Column("effective_time", DateTime(timezone=True)),
            Column("ingestion_time", DateTime(timezone=True), nullable=False),
            Column("open", Numeric, nullable=False),
            Column("high", Numeric, nullable=False),
            Column("low", Numeric, nullable=False),
            Column("close", Numeric, nullable=False),
            Column("volume", Numeric, nullable=False),
        )

    def append(
        self,
        dataset_version_id: UUID,
        bars: tuple[MarketBar, ...],
    ) -> None:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("MARKET_BAR_SINK_REQUIRES_DATASET_VERSION_ID")
        if not isinstance(bars, tuple) or any(not isinstance(bar, MarketBar) for bar in bars):
            raise TypeError("MARKET_BAR_SINK_REQUIRES_MARKET_BAR_TUPLE")

        with self._connection.begin_nested():
            for bar in bars:
                self._append_one(dataset_version_id, bar)

    def _append_one(self, dataset_version_id: UUID, bar: MarketBar) -> None:
        try:
            instrument_id = UUID(bar.instrument_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("MARKET_BAR_SINK_REQUIRES_UUID_INSTRUMENT_ID") from exc

        values = {
            "dataset_version_id": dataset_version_id,
            "instrument_id": instrument_id,
            "event_time": bar.event_time,
            "available_time": bar.available_time,
            "effective_time": bar.effective_time,
            "ingestion_time": bar.ingestion_time,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        identity_columns = (
            "dataset_version_id",
            "instrument_id",
            "event_time",
            "available_time",
            "ingestion_time",
        )
        statement = (
            pg_insert(self._market_bars)
            .values(**values)
            .on_conflict_do_nothing(index_elements=list(identity_columns))
            .returning(self._market_bars.c.market_bar_id)
        )
        inserted_id = self._connection.execute(statement).scalar_one_or_none()
        if inserted_id is not None:
            return

        existing = self._connection.execute(
            select(
                self._market_bars.c.effective_time,
                self._market_bars.c.open,
                self._market_bars.c.high,
                self._market_bars.c.low,
                self._market_bars.c.close,
                self._market_bars.c.volume,
            ).where(
                self._market_bars.c.dataset_version_id == dataset_version_id,
                self._market_bars.c.instrument_id == instrument_id,
                self._market_bars.c.event_time == bar.event_time,
                self._market_bars.c.available_time == bar.available_time,
                self._market_bars.c.ingestion_time == bar.ingestion_time,
            )
        ).mappings().one()
        expected_payload = {
            "effective_time": bar.effective_time,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        if any(existing[name] != value for name, value in expected_payload.items()):
            raise ValueError("MARKET_BAR_IDENTITY_CONFLICT")
