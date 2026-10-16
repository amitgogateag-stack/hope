"""Full migrated PostgreSQL schema, actual SELECT-only role and per-section gaps."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from hope.api.dashboard_snapshot import build_dashboard_snapshot
from hope.application.jobs import JobRunRecord, JobRunStatus, create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.no_trade import PaperNoTradeWriter
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_no_trade import SqlAlchemyPaperNoTradeRepository


@pytest.fixture
def migrated_dashboard_schema(postgres_engine):
    schema = "dashboard_m1_" + uuid4().hex
    with postgres_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        apply_migrations(connection, Path(__file__).resolve().parents[2] / "migrations")
    try:
        yield schema
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def read_snapshot(engine, schema):
    with engine.connect() as connection:
        connection.execute(text(f'SET search_path TO "{schema}", public'))
        connection.commit()
        result = build_dashboard_snapshot(connection)
        connection.rollback()
        connection.execute(text("RESET search_path"))
        connection.commit()
        return result


@pytest.mark.integration
def test_full_migrated_projection_distinguishes_verified_zero_and_recorded_metadata(postgres_engine, migrated_dashboard_schema):
    schema = migrated_dashboard_schema
    empty = read_snapshot(postgres_engine, schema)["projections"]
    assert empty["datasets"] == {"status": "AVAILABLE", "registered": 0, "pit_certified": 0}
    assert empty["strategies"] == {"status": "AVAILABLE", "versions": 0}
    assert empty["experiments"]["total"] == empty["decisions"]["total"] == 0
    assert empty["paper_activity"] == {"status": "AVAILABLE", "last_completed_at": None, "succeeded_no_trade": 0}
    assert empty["market_data"] == {"status": "AVAILABLE", "bar_count": 0, "latest_event_at": None,
                                    "latest_available_at": None, "freshness_policy": "UNKNOWN"}
    assert empty["portfolio"] == {"status": "UNAVAILABLE"}
    sid, vid, did, dvid, uid, uvid = (uuid4() for _ in range(6))
    with postgres_engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        inserts = [
            ("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,'dashboard-fixture','TEST')", {"id": sid}),
            ("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:id,:sid,'v1','fixture')", {"id": vid, "sid": sid}),
            ("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,'dashboard-fixture','GENERIC',TRUE)", {"id": did}),
            ("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label) VALUES (:id,:did,'v1','sealed')", {"id": dvid, "did": did}),
            ("INSERT INTO universes(universe_id,name) VALUES (:id,'dashboard-fixture')", {"id": uid}),
            ("INSERT INTO universe_versions(universe_version_id,universe_id,version,pit_certified,declared_member_count) VALUES (:id,:uid,'v1',TRUE,0)", {"id": uvid, "uid": uid}),
            ("INSERT INTO configuration_snapshots(configuration_hash,canonical_json) VALUES (:hash,'{}'::jsonb)", {"hash": "d" * 64}),
            ("INSERT INTO experiments(experiment_id,hypothesis,strategy_version_id,dataset_version_id,universe_version_id,configuration_hash,environment,status) VALUES ('dashboard-fixture','private hypothesis',:sid,:did,:uid,:hash,'BACKTEST','CREATED')", {"sid": vid, "did": dvid, "uid": uvid, "hash": "d" * 64}),
        ]
        for query, parameters in inserts:
            connection.execute(text(query), parameters)
        scheduled = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
        run = create_scheduled_job_run(f"paper:USA:{vid}:dashboard-no-trade", scheduled)
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run)
        assert PaperNoTradeWriter(SqlAlchemyPaperNoTradeRepository(connection)).record(
            PaperCycleContext(run), strategy_version=str(vid), decision_time=scheduled, inputs_hash="a" * 64,
        )
        assert jobs.complete(JobRunRecord(run, JobRunStatus.SUCCEEDED, scheduled + timedelta(minutes=1)))
        instrument, staging_version = uuid4(), uuid4()
        connection.execute(text("INSERT INTO instruments(instrument_id,canonical_symbol,exchange,status) VALUES (:id,'PRIVATE','TEST','ACTIVE')"), {"id": instrument})
        connection.execute(text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:id,:did,'market-stage','staging',FALSE)"), {"id": staging_version, "did": did})
        connection.execute(text("INSERT INTO market_bars(dataset_version_id,instrument_id,event_time,available_time,ingestion_time,open,high,low,close,volume) VALUES (:dataset,:instrument,:time,:time,:time,100,100,100,100,1)"), {"dataset": staging_version, "instrument": instrument, "time": scheduled})
    recorded = read_snapshot(postgres_engine, schema)["projections"]
    assert recorded["datasets"] == {"status": "AVAILABLE", "registered": 1, "pit_certified": 1}
    assert recorded["strategies"]["versions"] == 1
    assert recorded["experiments"]["total"] == recorded["experiments"]["by_status"]["CREATED"] == 1
    assert recorded["paper_activity"] == {"status": "AVAILABLE", "last_completed_at": (scheduled + timedelta(minutes=1)).isoformat(), "succeeded_no_trade": 1}
    assert recorded["market_data"]["bar_count"] == 1
    assert recorded["market_data"]["latest_event_at"] == scheduled.isoformat()
    assert recorded["market_data"]["freshness_policy"] == "UNKNOWN"
    assert "private hypothesis" not in str(recorded) and str(vid) not in str(recorded)
    with postgres_engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        future = datetime.now(timezone.utc) + timedelta(days=1)
        connection.execute(text("INSERT INTO market_bars(dataset_version_id,instrument_id,event_time,available_time,ingestion_time,open,high,low,close,volume) VALUES (:dataset,:instrument,:time,:time,:time,100,100,100,100,1)"), {"dataset": staging_version, "instrument": instrument, "time": future})
    uncertain = read_snapshot(postgres_engine, schema)
    assert uncertain["projections"]["market_data"] == {"status": "UNAVAILABLE"}
    assert uncertain["projections"]["datasets"]["registered"] == 1
    assert uncertain["paper"]["job_runs"]["succeeded"] == 1


@pytest.mark.integration
def test_missing_optional_select_permission_is_unavailable_without_poisoning_other_sections(postgres_engine, migrated_dashboard_schema):
    schema = migrated_dashboard_schema
    role = "dashboard_reader_" + uuid4().hex
    tables = ("research_runs", "job_runs", "paper_environment_control_events", "paper_effects", "audit_events",
              "strategy_versions", "experiments", "research_decisions", "paper_no_trade_decisions", "market_bars")
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
        names = ",".join(f'"{schema}"."{name}"' for name in tables)
        connection.execute(text(f'GRANT SELECT ON TABLE {names} TO "{role}"'))
    try:
        with postgres_engine.connect() as connection:
            try:
                connection.execute(text(f'SET search_path TO "{schema}", public'))
                connection.execute(text(f'SET ROLE "{role}"'))
                connection.commit()
                snapshot = build_dashboard_snapshot(connection)
                assert snapshot["projections"]["datasets"] == {"status": "UNAVAILABLE"}
                assert snapshot["projections"]["strategies"] == {"status": "AVAILABLE", "versions": 0}
                assert snapshot["projections"]["decisions"]["total"] == 0
                assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            finally:
                connection.rollback()
                connection.execute(text("RESET ROLE"))
                connection.execute(text("RESET search_path"))
                connection.commit()
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{role}"'))
            connection.execute(text(f'DROP ROLE "{role}"'))
