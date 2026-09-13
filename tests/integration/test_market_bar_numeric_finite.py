import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
@pytest.mark.parametrize("column", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("invalid_value", ["NaN", "Infinity", "-Infinity"])
def test_market_bar_numerics_must_be_finite(column: str, invalid_value: str) -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            dataset_id = uuid4()
            dataset_version_id = uuid4()
            instrument_id = uuid4()
            connection.execute(
                text("INSERT INTO datasets(dataset_id, name, source) VALUES (:id, 'finite-bars', 'TEST')"),
                {"id": dataset_id},
            )
            connection.execute(
                text("INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label) VALUES (:version_id, :dataset_id, 'v1', 'TEST')"),
                {"version_id": dataset_version_id, "dataset_id": dataset_id},
            )
            connection.execute(
                text("INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) VALUES (:id, 'FINITE-BAR', 'TEST', 'ACTIVE')"),
                {"id": instrument_id},
            )

            values = {"open": "100", "high": "101", "low": "99", "close": "100", "volume": "1000"}
            values[column] = invalid_value
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO market_bars(dataset_version_id, instrument_id, event_time, available_time, ingestion_time, open, high, low, close, volume) "
                            "VALUES (:dataset_version_id, :instrument_id, now(), now(), now(), CAST(:open AS NUMERIC), CAST(:high AS NUMERIC), CAST(:low AS NUMERIC), CAST(:close AS NUMERIC), CAST(:volume AS NUMERIC))"
                        ),
                        {"dataset_version_id": dataset_version_id, "instrument_id": instrument_id, **values},
                    )
        finally:
            transaction.rollback()
