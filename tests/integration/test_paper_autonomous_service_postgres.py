"""Synthetic service lifecycle using real PostgreSQL guards and scheduler.

Only the candidate catalog is injected to avoid inventing scientific promotion
history. Operational claims, effects, control locks and recovery remain real.
"""
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from hope.application.jobs import create_scheduled_job_run
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.application.paper import PaperEffectType, create_paper_effect
from hope.domain.strategy.candidates import StrategyCandidateState, StrategyMarket
from hope.infrastructure import paper_service as service
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository
from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
from hope.infrastructure.repositories.strategy_candidates import CurrentStrategyCandidateRecord, SqlAlchemyStrategyCandidateRepository
from hope.infrastructure.scheduling.paper import OperationalPaperJobBinding, OperationalPaperSchedule

NOW = datetime(2025, 9, 15, 14, tzinfo=UTC)
MIGRATIONS = Path(__file__).parents[2] / "migrations"


@pytest.mark.integration
def test_real_worker_process_termination_and_fresh_cli_restart(tmp_path):
    """A real child runs genuine certified lineage, commits, then restarts once."""
    import runpy
    from hope.domain.strategy.candidates import StrategyCandidateClassification
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    scheduled = datetime.now(UTC).replace(second=0, microsecond=0)
    helper = runpy.run_path(str(Path(__file__).with_name("m2_research_fixture.py")))
    configuration = {"parameters": {}, "paper_execution": {"market": "USA", "portfolio_id": str(uuid4()),
        "initial_cash": "1000", "quantity": "1", "side": "BUY",
        "risk_limits": {"max_position_notional": "500", "max_gross_exposure": "1000", "max_open_positions": 5},
        "cost_model_version": "m2-cost-v1", "commission_rate": "0.001", "slippage_bps": "2",
        "latency_milliseconds": 60000, "submission_delay_milliseconds": 0, "market_data_max_age_seconds": 120}}
    with engine.begin() as connection:
        apply_migrations(connection, MIGRATIONS)
        data = helper["seed"](connection, configuration=configuration, as_of=scheduled, include_quote=True)
    version = data["strategy_version_id"]
    module = tmp_path / "m2_process_fixture.py"
    module.write_text('''
from datetime import datetime, timedelta
from uuid import UUID
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.strategy.candidates import StrategyMarket
from hope.domain.strategy.models import Strategy, ParameterSnapshot
from hope.infrastructure.certified_paper import CertifiedPaperDefinition, CertifiedResearchPaperWork
from hope.infrastructure.paper_service import PaperServicePlan
from hope.infrastructure.scheduling.paper import OperationalPaperJobBinding, OperationalPaperSchedule
VERSION = UUID(VERSION_TEXT)
CLASSIFICATION = UUID(CLASSIFICATION_TEXT)
DATASET = UUID(DATASET_TEXT)
DECISION = DECISION_TEXT
SCHEDULED = datetime.fromisoformat(SCHEDULED_TEXT)
class NoTrade(Strategy):
    name = "controlled-child-fixture"
    version = "v1"
    def generate_signals(self, *args):
        return ()
def factory(connection,current):
    work = CertifiedResearchPaperWork(VERSION,StrategyMarket.USA,CLASSIFICATION,DECISION,DATASET,
        CertifiedPaperDefinition("TIME_SERIES_TREND","m2-synthetic-fixture",lambda _:NoTrade(),lambda _:ParameterSnapshot()))
    return PaperServicePlan((OperationalPaperJobBinding(VERSION,StrategyMarket.USA,"process",work),),
        (OperationalPaperSchedule(VERSION,StrategyMarket.USA,"process",timedelta(0)),),
        {StrategyMarket.USA:MarketSessionCalendar(((SCHEDULED,SCHEDULED+timedelta(hours=1)),))},
        SCHEDULED,SCHEDULED+timedelta(hours=1),timedelta(minutes=5))
'''.replace("VERSION_TEXT", repr(str(version))).replace("CLASSIFICATION_TEXT", repr(str(data["classification_id"])))
        .replace("DATASET_TEXT", repr(str(data["dataset_version_id"]))).replace("DECISION_TEXT", repr(data["research_decision_id"]))
        .replace("SCHEDULED_TEXT", repr(scheduled.isoformat())), encoding="utf-8")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tmp_path) + os.pathsep + os.pathsep.join(sys.path)
    status = tmp_path / "process-health.json"
    command = [sys.executable, "-m", "hope.api.paper_service", "--factory", "m2_process_fixture:factory",
               "--migrations-dir", str(MIGRATIONS), "--status-file", str(status), "--poll-seconds", "30"]
    process = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Certified child exited before durable acceptance")
            try:
                health = json.loads(status.read_text())
                if health["state"] == "CURRENT":
                    assert health["jobs_executed"] == 1
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
        else:
            pytest.fail("Certified child never published committed acceptance")
        process.terminate()
        process.wait(timeout=10)
        restarted = subprocess.run(command + ["--once"], env=environment, capture_output=True, timeout=25)
        assert restarted.returncode == 0
        assert json.loads(status.read_text())["jobs_skipped"] == 1
        run = create_scheduled_job_run(f"paper:USA:{version}:process", scheduled)
        assert _counts(engine, run) == (1, 1, 1)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM paper_strategy_authorizations WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM paper_certified_outcomes WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stderr.close()
        with engine.begin() as connection:
            SqlAlchemyStrategyCandidateRepository(connection).append(StrategyCandidateClassification(
                strategy_version_id=version, markets=frozenset({StrategyMarket.USA}), state=StrategyCandidateState.RESEARCH,
                rationale="Controlled certified child cleanup"))
        engine.dispose()


@pytest.fixture
def admitted_plan(monkeypatch):
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=5000"})
    strategy, version = uuid4(), uuid4()
    with engine.begin() as connection:
        apply_migrations(connection, MIGRATIONS)
        connection.execute(text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"), {"id": strategy, "name": f"SERVICE-{strategy}"})
        connection.execute(text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:id,:strategy,'v1','service-test')"), {"id": version, "strategy": strategy})
    catalog = CurrentStrategyCandidateRecord(classification_id=uuid4(), classification_sequence=1,
        strategy_version_id=version, family="TEST", markets=(StrategyMarket.USA,),
        state=StrategyCandidateState.OPERATIONAL_CANDIDATE, research_decision_id="SYNTHETIC-TEST-CATALOG",
        rationale="Synthetic service fixture, never production admission", created_at=NOW)
    monkeypatch.setattr(service.SqlAlchemyStrategyCandidateRepository, "current", lambda _: [catalog])
    def no_trade(runtime):
        runtime.record_no_trade(strategy_version="v1", decision_time=NOW, inputs_hash="a" * 64)
    def plan(work=no_trade):
        return service.PaperServicePlan(
            bindings=(OperationalPaperJobBinding(version, StrategyMarket.USA, "service", work),),
            schedules=(OperationalPaperSchedule(version, StrategyMarket.USA, "service", timedelta(0)),),
            calendars={StrategyMarket.USA: MarketSessionCalendar(((NOW, NOW + timedelta(hours=1)),))},
            start=NOW, end=NOW + timedelta(hours=1), max_lateness=timedelta(minutes=1))
    run = create_scheduled_job_run(plan().bindings[0].durable_job_key, NOW)
    try:
        yield engine, plan, run
    finally:
        with engine.begin() as connection:
            connection.execute(text("UPDATE job_runs SET status='FAILED',completed_at=clock_timestamp(),failure_code='EXPECTED_SERVICE_TEST_CLEANUP' WHERE job_run_id=:id AND status='CLAIMED'"), {"id": run.job_run_id})
            control = SqlAlchemyPaperEnvironmentControlRepository(connection)
            if control.current_state() == "HALTED":
                control.transition("HALTED", "RUNNING", "service fixture cleanup", actor="integration-test")
        engine.dispose()


def _cycle(engine, factory):
    return service.run_service_cycle(engine, factory, migrations_dir=MIGRATIONS, now=lambda: NOW)


def _counts(engine, run):
    with engine.connect() as connection:
        return tuple(connection.execute(text(f"SELECT count(*) FROM {table} WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one()
            for table in ("job_runs", "paper_effects", "paper_no_trade_decisions"))


@pytest.mark.integration
def test_clean_service_cycle_reboot_retry_and_schema_readonly(admitted_plan, tmp_path):
    engine, plan, run = admitted_plan
    def factory(connection, current):
        assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        assert current == NOW
        return plan()
    assert _cycle(engine, factory) == (1, 0)
    engine.dispose()  # next process/pool starts from durable database truth
    assert _cycle(engine, factory) == (0, 1)
    assert _counts(engine, run) == (1, 1, 1)
    status = tmp_path / "health.json"
    assert service.run_service(engine, factory, migrations_dir=MIGRATIONS, status_file=status,
        once=True, now=lambda: NOW) == 0
    health = json.loads(status.read_text())
    assert health["state"] == "STOPPED" and health["schema_compatibility"] == "COMPATIBLE"
    assert health["last_success_at"] is not None
    assert health["jobs_skipped"] == 1 and health["live_enabled"] is False


@pytest.mark.integration
def test_two_service_workers_never_duplicate_no_trade(admitted_plan):
    engine, plan, run = admitted_plan
    barrier = Barrier(2)
    def worker():
        barrier.wait(timeout=5)
        try:
            return _cycle(engine, lambda *_: plan())
        except RuntimeError as exc:
            assert str(exc) == "PAPER_SCHEDULER_CONCURRENT_RUN"
            return (0, 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert sum(result[0] for result in results) == 1
    assert _cycle(engine, lambda *_: plan()) == (0, 1)
    assert _counts(engine, run) == (1, 1, 1)


@pytest.mark.integration
def test_halt_and_incompatible_schema_precede_factory_and_claim(admitted_plan, tmp_path):
    engine, plan, run = admitted_plan
    called = []
    def factory(*_):
        called.append(True)
        return plan()
    with pytest.raises(RuntimeError, match="SCHEMA_FILES_UNAVAILABLE"):
        service.run_service_cycle(engine, factory, migrations_dir=tmp_path, now=lambda: NOW)
    (tmp_path / "unexpected.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(RuntimeError, match="SCHEMA_INCOMPATIBLE"):
        service.run_service_cycle(engine, factory, migrations_dir=tmp_path, now=lambda: NOW)
    with engine.begin() as connection:
        SqlAlchemyPaperEnvironmentControlRepository(connection).transition("RUNNING", "HALTED", "service test halt", actor="integration-test")
    with pytest.raises(RuntimeError, match="PAPER_ENVIRONMENT_HALTED"):
        _cycle(engine, factory)
    assert called == [] and _counts(engine, run) == (0, 0, 0)


@pytest.mark.integration
def test_partial_durable_claim_is_never_replayed(admitted_plan):
    engine, plan, run = admitted_plan
    with engine.begin() as connection:
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        assert SqlAlchemyPaperEffectRepository(connection).record(create_paper_effect(run, PaperEffectType.SIGNAL, uuid4(), "b" * 64))
    with pytest.raises(RuntimeError, match="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"):
        _cycle(engine, lambda *_: plan())
    assert _counts(engine, run) == (1, 1, 0)
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run).status.value == "CLAIMED"


@pytest.mark.integration
def test_backend_death_before_work_commit_restarts_without_effects(admitted_plan):
    engine, plan, run = admitted_plan
    class CrashWork:
        def bind(self, connection):
            pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            def execute(runtime):
                runtime.record_no_trade(strategy_version="v1", decision_time=NOW, inputs_hash="a" * 64)
                with engine.begin() as killer:
                    assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}).scalar_one()
                connection.execute(text("SELECT 1"))
            return execute
    with pytest.raises(SQLAlchemyError):
        _cycle(engine, lambda *_: plan(CrashWork()))
    assert _counts(engine, run) == (0, 0, 0)
    assert _cycle(engine, lambda *_: plan()) == (1, 0)
    assert _counts(engine, run) == (1, 1, 1)


@pytest.mark.integration
def test_broken_preflight_connection_recovers_next_cycle(admitted_plan, tmp_path):
    engine, plan, run = admitted_plan
    attempts = []
    def factory(connection, _):
        attempts.append(True)
        if len(attempts) == 1:
            connection.invalidate()
            connection.execute(text("SELECT 1"))
        return plan()
    status = tmp_path / "health.json"
    assert service.run_service(engine, factory, migrations_dir=MIGRATIONS, status_file=status, once=True, now=lambda: NOW) == 1
    assert json.loads(status.read_text())["error_code"] == "PAPER_SERVICE_DATABASE_UNAVAILABLE"
    assert _counts(engine, run) == (0, 0, 0)
    assert service.run_service(engine, factory, migrations_dir=MIGRATIONS, status_file=status, once=True, now=lambda: NOW) == 0
    assert _counts(engine, run) == (1, 1, 1)

@pytest.mark.integration
def test_missing_catalog_and_missing_plan_cannot_authorize_work(admitted_plan, monkeypatch):
    engine, plan, run = admitted_plan
    with pytest.raises(RuntimeError, match="PAPER_SERVICE_PLAN_UNAVAILABLE"):
        _cycle(engine, lambda *_: None)
    monkeypatch.setattr(service.SqlAlchemyStrategyCandidateRepository, "current", lambda _: [])
    with pytest.raises(ValueError, match="PAPER_ORCHESTRATION_CANDIDATE_NOT_CURRENT"):
        _cycle(engine, lambda *_: plan())
    assert _counts(engine, run) == (0, 0, 0)


@pytest.mark.integration
def test_candidate_demotion_after_plan_validation_precedes_claim(admitted_plan, monkeypatch):
    """A fresh protected catalog read rejects a post-validation demotion."""
    engine, plan, run = admitted_plan
    operational = service.SqlAlchemyStrategyCandidateRepository.current(None)
    demoted = [
        operational[0].model_copy(
            update={"state": StrategyCandidateState.BACKUP_CANDIDATE}
        )
    ]
    reads = iter((operational, demoted))
    monkeypatch.setattr(
        service.SqlAlchemyStrategyCandidateRepository,
        "current",
        lambda _: next(reads),
    )

    with pytest.raises(ValueError, match="PAPER_ORCHESTRATION_CANDIDATE_NOT_OPERATIONAL"):
        _cycle(engine, lambda *_: plan())

    assert _counts(engine, run) == (0, 0, 0)


@pytest.mark.integration
def test_candidate_transition_lock_is_held_through_paper_effect_commit(admitted_plan):
    """The DB trigger's transition lock cannot be acquired while work executes."""
    engine, plan, run = admitted_plan

    def no_trade_while_probing_transition_lock(runtime):
        with engine.connect() as probe:
            assert probe.execute(
                text(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtext('hope:strategy-candidate-capacity')::bigint)"
                )
            ).scalar_one() is False
        runtime.record_no_trade(
            strategy_version="v1",
            decision_time=NOW,
            inputs_hash="d" * 64,
        )

    assert _cycle(engine, lambda *_: plan(no_trade_while_probing_transition_lock)) == (1, 0)
    assert _counts(engine, run) == (1, 1, 1)


@pytest.mark.integration
def test_factory_writes_rejected_and_closed_market_is_valid_idle(admitted_plan):
    from dataclasses import replace
    engine, plan, run = admitted_plan
    def bad_factory(connection, _):
        connection.execute(text("INSERT INTO universes(universe_id,name) VALUES (:id,'forbidden-service-factory')"), {"id": uuid4()})
        return plan()
    with pytest.raises(SQLAlchemyError, match="read-only"):
        _cycle(engine, bad_factory)
    assert _counts(engine, run) == (0, 0, 0)
    assert _cycle(engine, lambda *_: replace(plan(), calendars={StrategyMarket.USA: MarketSessionCalendar(())})) == (0, 0)
    assert _counts(engine, run) == (0, 0, 0)


@pytest.mark.integration
def test_lost_service_result_after_commit_restarts_from_durable_truth(admitted_plan, monkeypatch, tmp_path):
    from sqlalchemy.exc import OperationalError
    engine, plan, run = admitted_plan
    original = service.run_due_operational_paper_jobs
    def lose_result(*args, **kwargs):
        original(*args, **kwargs)
        raise OperationalError("SIMULATED_LOST_CYCLE_RESULT_AFTER_COMMIT", {}, Exception("connection lost"))
    status = tmp_path / "health.json"
    with monkeypatch.context() as patch:
        patch.setattr(service, "run_due_operational_paper_jobs", lose_result)
        assert service.run_service(engine, lambda *_: plan(), migrations_dir=MIGRATIONS,
            status_file=status, once=True, now=lambda: NOW) == 1
    assert _counts(engine, run) == (1, 1, 1)
    assert json.loads(status.read_text())["state"] == "UNAVAILABLE"
    assert _cycle(engine, lambda *_: plan()) == (0, 1)
    assert _counts(engine, run) == (1, 1, 1)
