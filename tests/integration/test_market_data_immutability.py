import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_market_data_staging_seals_once_and_then_becomes_immutable() -> None:
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
            t0 = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id, "symbol": f"IMM-{instrument_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                    "VALUES (:dataset_id, :name, 'TEST', TRUE)"
                ),
                {"dataset_id": dataset_id, "name": f"immutability-{dataset_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions("
                    "dataset_version_id, dataset_id, version, vintage_label, immutable"
                    ") VALUES (:version_id, :dataset_id, 'v1', 'staging', FALSE)"
                ),
                {"version_id": dataset_version_id, "dataset_id": dataset_id},
            )

            connection.execute(
                text(
                    "INSERT INTO market_bars("
                    "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                    "open, high, low, close, volume"
                    ") VALUES ("
                    ":version_id, :instrument_id, :event_time, :available_time, :ingestion_time, "
                    "100, 101, 99, 100, 1000)"
                ),
                {
                    "version_id": dataset_version_id,
                    "instrument_id": instrument_id,
                    "event_time": t0,
                    "available_time": t0,
                    "ingestion_time": t0,
                },
            )

            with pytest.raises(IntegrityError, match="MARKET_BAR_HISTORY_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE market_bars SET close = 100.5 "
                            "WHERE dataset_version_id = :version_id"
                        ),
                        {"version_id": dataset_version_id},
                    )

            with pytest.raises(IntegrityError, match="MARKET_BAR_HISTORY_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM market_bars WHERE dataset_version_id = :version_id"
                        ),
                        {"version_id": dataset_version_id},
                    )

            connection.execute(
                text(
                    "UPDATE dataset_versions SET immutable = TRUE, vintage_label = 'sealed' "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            )

            with pytest.raises(IntegrityError, match="MARKET_BAR_IMMUTABLE_DATASET_VERSION"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO market_bars("
                            "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                            "open, high, low, close, volume"
                            ") VALUES ("
                            ":version_id, :instrument_id, :event_time, :available_time, :ingestion_time, "
                            "101, 102, 100, 101, 1000)"
                        ),
                        {
                            "version_id": dataset_version_id,
                            "instrument_id": instrument_id,
                            "event_time": t0.replace(minute=31),
                            "available_time": t0.replace(minute=31),
                            "ingestion_time": t0.replace(minute=31),
                        },
                    )

            with pytest.raises(IntegrityError, match="DATASET_VERSION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE dataset_versions SET immutable = FALSE "
                            "WHERE dataset_version_id = :version_id"
                        ),
                        {"version_id": dataset_version_id},
                    )

            with pytest.raises(IntegrityError, match="DATASET_VERSION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE dataset_versions SET vintage_label = 'rewritten' "
                            "WHERE dataset_version_id = :version_id"
                        ),
                        {"version_id": dataset_version_id},
                    )

            immutable = connection.execute(
                text(
                    "SELECT immutable FROM dataset_versions "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).scalar_one()
            bar_count = connection.execute(
                text(
                    "SELECT count(*) FROM market_bars "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).scalar_one()
            assert immutable is True
            assert bar_count == 1
        finally:
            transaction.rollback()
