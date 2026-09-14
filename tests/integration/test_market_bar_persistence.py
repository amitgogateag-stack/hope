import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.domain.market_data.models import MarketBar
from hope.infrastructure.market_data.ingestion import NormalizedMarketBarBatch
from hope.infrastructure.market_data.persistence import persist_normalized_batch
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_market_bar_sink_is_exact_retry_idempotent_and_conflict_atomic() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            instrument_id = uuid4()
            dataset_id = uuid4()
            dataset_version_id = uuid4()
            t0 = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id, "symbol": f"SINK-{instrument_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                    "VALUES (:dataset_id, :name, 'TEST', TRUE)"
                ),
                {"dataset_id": dataset_id, "name": f"sink-{dataset_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions("
                    "dataset_version_id, dataset_id, version, vintage_label, immutable"
                    ") VALUES (:version_id, :dataset_id, 'v1', 'staging', FALSE)"
                ),
                {"version_id": dataset_version_id, "dataset_id": dataset_id},
            )

            original = _bar(instrument_id, t0)
            sink = SqlAlchemyMarketBarSink(connection)
            batch = NormalizedMarketBarBatch(bars=(original,), rejections=(), input_count=1)

            persist_normalized_batch(sink, dataset_version_id, batch)
            persist_normalized_batch(sink, dataset_version_id, batch)

            count = connection.execute(
                text(
                    "SELECT count(*) FROM market_bars "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).scalar_one()
            assert count == 1

            later = _bar(instrument_id, t0 + timedelta(minutes=1))
            conflicting = original.model_copy(update={"close": Decimal("100.5")})
            conflict_batch = NormalizedMarketBarBatch(
                bars=(later, conflicting),
                rejections=(),
                input_count=2,
            )

            with pytest.raises(ValueError, match="MARKET_BAR_IDENTITY_CONFLICT"):
                persist_normalized_batch(sink, dataset_version_id, conflict_batch)

            rows = connection.execute(
                text(
                    "SELECT event_time, close FROM market_bars "
                    "WHERE dataset_version_id = :version_id ORDER BY event_time"
                ),
                {"version_id": dataset_version_id},
            ).mappings().all()
            assert len(rows) == 1
            assert rows[0]["event_time"] == t0
            assert rows[0]["close"] == Decimal("100")
        finally:
            transaction.rollback()


def _bar(instrument_id, timestamp: datetime) -> MarketBar:
    return MarketBar(
        instrument_id=str(instrument_id),
        event_time=timestamp,
        available_time=timestamp,
        effective_time=None,
        ingestion_time=timestamp,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )
