from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.application.market_data.lab_import import ingest_verified_export_to_lab
from hope.infrastructure.market_data.file_provider import MarketDataFileEvidence
from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_lab_export_import_is_atomic_manifest_bound_and_sealed(postgres_engine, tmp_path, request):
    lab_database_name = f"hope_lab_import_{uuid4().hex}"
    admin_engine = create_engine(postgres_engine.url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as admin:
        admin.exec_driver_sql(f'CREATE DATABASE "{lab_database_name}"')
    lab_engine = create_engine(postgres_engine.url.set(database=lab_database_name))

    def cleanup() -> None:
        lab_engine.dispose(close=True)
        with admin_engine.connect() as admin:
            admin.exec_driver_sql(f'DROP DATABASE IF EXISTS "{lab_database_name}" WITH (FORCE)')
        admin_engine.dispose(close=True)

    request.addfinalizer(cleanup)
    postgres_engine = lab_engine
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with postgres_engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            database_name = connection.execute(text("SELECT current_database()")).scalar_one()
            assert database_name == lab_database_name
            instrument_id = uuid4()
            dataset_id = uuid4()
            good_version_id = uuid4()
            failed_version_id = uuid4()
            universe_id = uuid4()
            universe_version_id = uuid4()
            t0 = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
            t1 = t0 + timedelta(minutes=1)
            t2 = t1 + timedelta(minutes=1)
            connection.execute(text(
                "INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) "
                "VALUES (:id,:symbol,'TEST','ACTIVE')"
            ), {"id": instrument_id, "symbol": f"LAB-{instrument_id}"})
            connection.execute(text(
                "INSERT INTO datasets(dataset_id,name,source,pit_certified) "
                "VALUES (:id,:name,'TEST_EXPORT',TRUE)"
            ), {"id": dataset_id, "name": f"lab-import-{dataset_id}"})
            connection.execute(text(
                "INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) "
                "VALUES (:good,:dataset,'good','staging',FALSE),"
                "(:failed,:dataset,'failed','staging',FALSE)"
            ), {"good": good_version_id, "failed": failed_version_id, "dataset": dataset_id})
            connection.execute(text(
                "INSERT INTO universes(universe_id,name) VALUES (:id,:name)"
            ), {"id": universe_id, "name": f"lab-import-{universe_id}"})
            connection.execute(text(
                "INSERT INTO universe_versions(universe_version_id,universe_id,version,pit_certified,declared_member_count) "
                "VALUES (:id,:universe,'v1',TRUE,1)"
            ), {"id": universe_version_id, "universe": universe_id})
            connection.execute(text(
                "INSERT INTO universe_members(universe_version_id,instrument_id,valid_from,valid_to) "
                "VALUES (:universe,:instrument,:start,:end)"
            ), {"universe": universe_version_id, "instrument": instrument_id, "start": t0, "end": t2})

            market_request = MarketDataRequest(
                source="TEST_EXPORT", source_symbols=("ABC",), start=t0, end=t2,
                interval=timedelta(minutes=1),
            )
            payload = (
                "source,source_symbol,event_time,available_time,ingestion_time,effective_time,open,high,low,close,volume\n"
                "TEST_EXPORT,ABC,2026-11-20T14:30:00Z,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,,100,101,99,100,1000\n"
                "TEST_EXPORT,ABC,2026-11-20T14:31:00Z,2026-11-20T14:32:00Z,2026-11-20T14:33:00Z,,101,102,100,101,1100\n"
            ).encode()
            export = tmp_path / "lab-export.csv"
            export.write_bytes(payload)
            evidence = MarketDataFileEvidence(
                source="TEST_EXPORT",
                payload_sha256=hashlib.sha256(payload).hexdigest(),
                provider_contract_sha256="d" * 64,
                observed_at=t2 + timedelta(minutes=2),
            )
            receipt = ingest_verified_export_to_lab(
                connection,
                expected_database_name=database_name,
                dataset_version_id=good_version_id,
                universe_version_id=universe_version_id,
                requests=(market_request,),
                identity_map={("TEST_EXPORT", "ABC"): instrument_id},
                export_path=export,
                evidence=evidence,
            )
            assert receipt["bar_count"] == 2 and receipt["sealed"] is True
            assert receipt["paper_authority"] is False
            assert receipt["release_admission"] is False
            assert receipt["live_enabled"] is False

            export.write_bytes(payload + b"\n")
            with pytest.raises(ValueError, match="PAYLOAD_DIGEST_MISMATCH"):
                ingest_verified_export_to_lab(
                    connection,
                    expected_database_name=database_name,
                    dataset_version_id=failed_version_id,
                    universe_version_id=universe_version_id,
                    requests=(market_request,),
                    identity_map={("TEST_EXPORT", "ABC"): instrument_id},
                    export_path=export,
                    evidence=evidence,
                )
            assert connection.execute(text(
                "SELECT count(*) FROM market_data_coverage_manifests WHERE dataset_version_id=:id"
            ), {"id": failed_version_id}).scalar_one() == 0
            assert connection.execute(text(
                "SELECT count(*) FROM market_bars WHERE dataset_version_id=:id"
            ), {"id": failed_version_id}).scalar_one() == 0
            assert connection.execute(text(
                "SELECT immutable FROM dataset_versions WHERE dataset_version_id=:id"
            ), {"id": failed_version_id}).scalar_one() is False
        finally:
            transaction.rollback()
