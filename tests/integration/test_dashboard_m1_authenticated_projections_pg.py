"""Full migrated PostgreSQL schema, actual SELECT-only role and per-section gaps."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import runpy

import pytest
from sqlalchemy import text
from sqlalchemy import create_engine

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
    assert "private hypothesis" not in str(recorded)
    # M2 intentionally permits only bounded strategy-version UUIDs; all family
    # text is constrained to its fixed vocabulary and arbitrary labels collapse.
    assert recorded["strategy_catalog"]["rows"] == [{"strategy_version_id": str(vid), "family_code": "OTHER", "candidate_state": "UNCLASSIFIED", "markets": []}]
    assert recorded["dataset_certification"] == {"status": "AVAILABLE", "version_total": 2, "sealed_versions": 1, "pit_certified_versions": 1, "manifest_backed_sealed_versions": 0}
    assert recorded["execution_ledger"]["fills"] == 0
    assert recorded["decision_activity"]["no_trade_records"] == 1
    assert recorded["certified_chain"]["eligibility_policy"] == "UNKNOWN"
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
                assert snapshot["projections"]["strategy_catalog"] == {"status": "UNAVAILABLE"}
                assert snapshot["projections"]["certified_chain"] == {"status": "UNAVAILABLE"}
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


@pytest.mark.integration
def test_m2_strategy_catalog_is_sorted_bounded_and_marks_truncation(postgres_engine, migrated_dashboard_schema):
    schema, strategy = migrated_dashboard_schema, uuid4()
    versions = [uuid4() for _ in range(23)]
    with postgres_engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        connection.execute(text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,'bounded-catalog','TIME_SERIES_TREND')"), {"id": strategy})
        connection.execute(text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:id,:sid,:version,'fixture')"), [{"id": version, "sid": strategy, "version": str(index)} for index, version in enumerate(versions)])
    catalog = read_snapshot(postgres_engine, schema)["projections"]["strategy_catalog"]
    assert catalog["total"] == 23 and catalog["truncated"] is True
    assert len(catalog["rows"]) == 20
    assert [row["strategy_version_id"] for row in catalog["rows"]] == sorted(map(str, versions))[:20]
    assert all(row["family_code"] == "TIME_SERIES_TREND" for row in catalog["rows"])


@pytest.mark.integration
def test_m2_authorization_history_survives_demotion_without_implying_current_eligibility(postgres_engine, migrated_dashboard_schema):
    from hope.domain.strategy.candidates import StrategyCandidateClassification, StrategyCandidateState
    from hope.infrastructure.repositories.strategy_candidates import SqlAlchemyStrategyCandidateRepository
    seed = runpy.run_path(str(Path(__file__).with_name("m2_research_fixture.py")))["seed"]
    schema = migrated_dashboard_schema
    with postgres_engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        fixture = seed(connection)
        parameters = {"strategy": fixture["strategy_version_id"], "market": fixture["market"].value,
                      "classification": fixture["classification_id"], "decision": fixture["research_decision_id"]}
        connection.execute(text("SELECT hope_assert_current_paper_eligibility(:strategy,:market,:classification,:decision)"), parameters)
        run = create_scheduled_job_run(f"paper:USA:{fixture['strategy_version_id']}:dashboard-certified", fixture["as_of"])
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        connection.execute(text("INSERT INTO paper_strategy_authorizations(job_run_id,strategy_version_id,market,classification_id,research_decision_id) VALUES (:job,:strategy,:market,:classification,:decision)"), {**parameters, "job": run.job_run_id})
    before = read_snapshot(postgres_engine, schema)["projections"]
    assert before["certified_chain"]["authorization_receipts"] == 1
    assert before["certified_chain"]["operational_classifications"] == 1
    assert before["decisions"]["by_verdict"]["SUPPORTED"] == 1
    assert before["dataset_certification"]["manifest_backed_sealed_versions"] == 1
    with postgres_engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        assert SqlAlchemyStrategyCandidateRepository(connection).append(StrategyCandidateClassification(
            strategy_version_id=fixture["strategy_version_id"], markets=frozenset({fixture["market"]}),
            state=StrategyCandidateState.RESEARCH, rationale="Controlled dashboard test demotion; never production authorization",
        ))
    after = read_snapshot(postgres_engine, schema)["projections"]
    assert after["certified_chain"]["authorization_receipts"] == 1
    assert after["certified_chain"]["operational_classifications"] == 0
    assert after["certified_chain"]["eligibility_policy"] == "UNKNOWN"
    assert after["strategy_catalog"]["rows"][0]["candidate_state"] == "RESEARCH"


@pytest.mark.integration
@pytest.mark.parametrize("path", ["NO_TRADE", "RISK_BLOCKED", "FILLED"])
def test_m2_dashboard_observes_real_certified_bridge_outcomes(postgres_engine, migrated_dashboard_schema, path):
    from hope.infrastructure.certified_paper import CertifiedPaperDefinition, CertifiedResearchPaperWork
    from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry, run_paper_once
    from hope.application.paper import PaperCycleOutcome
    from hope.domain.strategy.models import ParameterSnapshot
    schema = migrated_dashboard_schema
    helpers = runpy.run_path(str(Path(__file__).with_name("test_certified_paper_bridge.py")))
    seed = runpy.run_path(str(Path(__file__).with_name("m2_research_fixture.py")))["seed"]
    engine = create_engine(postgres_engine.url.update_query_dict({"options": f"-csearch_path={schema},public"}))
    try:
        with engine.begin() as connection:
            data = seed(connection, configuration=helpers["configuration"](blocked=path == "RISK_BLOCKED"), include_quote=True)
        run = create_scheduled_job_run(f"paper:USA:{data['strategy_version_id']}:dashboard-bridge", data["as_of"])
        strategy = helpers["_Decision"](run, path != "NO_TRADE")
        work = CertifiedResearchPaperWork(data["strategy_version_id"], data["market"], data["classification_id"],
            data["research_decision_id"], data["dataset_version_id"],
            CertifiedPaperDefinition(data["family"], data["code_commit"], lambda _: strategy, lambda _: ParameterSnapshot()))
        registry = PaperJobRegistry([PaperJobDefinition(run.job_key, work)])
        clock = lambda: data["as_of"] + timedelta(minutes=1)
        assert run_paper_once(engine, run, registry, now=clock) is PaperCycleOutcome.EXECUTED
        assert run_paper_once(engine, run, registry, now=clock) is PaperCycleOutcome.SKIPPED_TERMINAL
        observed = read_snapshot(postgres_engine, schema)["projections"]
        outcome = observed["certified_outcomes"]
        assert outcome["status"] == "AVAILABLE" and outcome["total"] == 1
        assert outcome["by_outcome"] == {name: int(name == path) for name in ("NO_TRADE", "RISK_BLOCKED", "FILLED")}
        assert outcome["claimed_records"] == 0
        assert outcome["risk_blocked_reasons"]["MAX_POSITION_NOTIONAL_EXCEEDED"] == int(path == "RISK_BLOCKED")
        assert observed["certified_chain"]["authorization_receipts"] == 1
        assert observed["certified_chain"]["eligibility_policy"] == "UNKNOWN"
        assert observed["execution_ledger"]["fills"] == int(path == "FILLED")
        assert observed["execution_ledger"]["accounting_receipts"] == int(path == "FILLED")
        assert observed["execution_ledger"]["applications_without_pnl"] == 0
    finally:
        engine.dispose()
