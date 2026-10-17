"""Certified stored research through existing PAPER risk, fill and accounting."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import runpy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleOutcome
from hope.application.paper.context import PaperCycleContext
from hope.domain.signal.models import Signal, SignalType
from hope.domain.strategy.candidates import StrategyCandidateClassification, StrategyCandidateState, StrategyMarket
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.infrastructure.certified_paper import CertifiedPaperDefinition, CertifiedResearchPaperWork
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry, run_paper_once
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_portfolio import SqlAlchemyPaperPortfolioRepository
from hope.infrastructure.repositories.strategy_candidates import SqlAlchemyStrategyCandidateRepository

_seed = runpy.run_path(str(Path(__file__).with_name("m2_research_fixture.py")))["seed"]


def configuration(*, blocked=False):
    return {"parameters": {}, "paper_execution": {
        "market": "USA", "portfolio_id": str(uuid4()), "initial_cash": "1000", "quantity": "2", "side": "BUY",
        "risk_limits": {"max_position_notional": "50" if blocked else "500", "max_gross_exposure": "1000", "max_open_positions": 5},
        "cost_model_version": "m2-cost-v1", "commission_rate": "0.001", "slippage_bps": "2",
        "latency_milliseconds": 60000, "submission_delay_milliseconds": 0, "market_data_max_age_seconds": 120}}


class _Decision(Strategy):
    name = "controlled-m2-fixture"
    version = "v1"
    def __init__(self, run, emit):
        self.run, self.emit, self.calls = run, emit, []
    def generate_signals(self, market_context, universe, universe_members, parameters, inputs_hash):
        self.calls.append(market_context)
        if not self.emit:
            return ()
        instrument = universe_members[0].instrument_id
        values = dict(instrument_id=instrument, strategy_version=self.version, decision_time=market_context.as_of,
            signal_type=SignalType.ENTRY, conviction=Decimal("0.8"), inputs_hash=inputs_hash)
        return (Signal(signal_id=PaperCycleContext(self.run).signal_id(**values), **values),)


@pytest.fixture
def certified_case():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=10000"})
    with engine.begin() as connection:
        apply_migrations(connection, Path(__file__).parents[2] / "migrations")
    created = []
    def make(*, emit=False, blocked=False, market=StrategyMarket.USA, certified=True, instrument_market=None):
        settings = configuration(blocked=blocked)
        settings["paper_execution"]["market"] = market.value
        with engine.begin() as connection:
            data = _seed(connection, configuration=settings, market=instrument_market or market, include_quote=True,
                as_of=datetime(2025, 9, 15, 4 if market is StrategyMarket.INDIA else 14, tzinfo=UTC), certified=certified)
        if instrument_market is not None and instrument_market is not market:
            definition = StrategyCandidateClassification(strategy_version_id=data["strategy_version_id"],
                markets=frozenset({market}), state=StrategyCandidateState.OPERATIONAL_CANDIDATE,
                research_decision_id=data["research_decision_id"], rationale="Synthetic mismatched instrument-market scope for negative test")
            with engine.begin() as connection:
                candidates = SqlAlchemyStrategyCandidateRepository(connection)
                assert candidates.append(definition)
                data["classification_id"] = candidates.deterministic_id(definition)
                data["market"] = market
        created.append(data)
        run = create_scheduled_job_run(f"paper:{market.value}:{data['strategy_version_id']}:certified", data["as_of"])
        strategy = _Decision(run, emit)
        work = CertifiedResearchPaperWork(data["strategy_version_id"], market, data["classification_id"], data["research_decision_id"], data["dataset_version_id"],
            CertifiedPaperDefinition(data["family"], data["code_commit"], lambda _: strategy, lambda _: ParameterSnapshot()))
        registry = PaperJobRegistry([PaperJobDefinition(run.job_key, work)])
        return data, run, strategy, work, registry
    try:
        yield engine, make
    finally:
        with engine.begin() as connection:
            for data in created:
                connection.execute(text("UPDATE job_runs SET status='FAILED',completed_at=clock_timestamp(),failure_code='EXPECTED_M2_BRIDGE_TEST_CLEANUP' WHERE status='CLAIMED' AND job_key LIKE :prefix"), {"prefix": f"paper:%:{data['strategy_version_id']}:%"})
                SqlAlchemyStrategyCandidateRepository(connection).append(StrategyCandidateClassification(strategy_version_id=data["strategy_version_id"], markets=frozenset({data["market"]}), state=StrategyCandidateState.RESEARCH, rationale=f"Synthetic M2 bridge fixture cleanup {uuid4()}"))
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("market", [StrategyMarket.USA, StrategyMarket.INDIA])
@pytest.mark.parametrize("path", ["NO_TRADE", "FILLED", "RISK_BLOCKED"])
def test_certified_research_to_paper_durable_outcome_and_restart(certified_case, market, path):
    engine, make = certified_case
    data, run, strategy, work, registry = make(emit=path != "NO_TRADE", blocked=path == "RISK_BLOCKED", market=market)
    clock = lambda: data["as_of"] + timedelta(minutes=1)
    assert run_paper_once(engine, run, registry, now=clock) is PaperCycleOutcome.EXECUTED
    assert len(strategy.calls) == 1
    assert run_paper_once(engine, run, registry, now=clock) is PaperCycleOutcome.SKIPPED_TERMINAL
    assert len(strategy.calls) == 1
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run).status.value == "SUCCEEDED"
        assert connection.execute(text("SELECT count(*) FROM paper_strategy_authorizations WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 1
        outcome = connection.execute(text("SELECT outcome,reason_code,source_run_id FROM paper_certified_outcomes WHERE job_run_id=:id"), {"id": run.job_run_id}).one()
        assert outcome.outcome == path and outcome.source_run_id == data["variant_run_id"]
        expected_effects = 5 if path == "FILLED" else 1
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == expected_effects
        if path == "FILLED":
            ledger = SqlAlchemyPaperPortfolioRepository(connection).load_ledger(UUID(data["configuration"]["paper_execution"]["portfolio_id"]))
            assert ledger.state.cash == Decimal("799.75996")
            assert ledger.state.positions[data["instrument_id"]].quantity == Decimal("2")
            assert ledger.state.positions[data["instrument_id"]].average_price == Decimal("100.02")
            assert ledger.state.positions[data["instrument_id"]].total_commission == Decimal("0.20004")
            assert connection.execute(text("SELECT count(*) FROM paper_portfolio_pnl_events WHERE portfolio_id=:id"), {"id": data["configuration"]["paper_execution"]["portfolio_id"]}).scalar_one() == 1
        else:
            assert connection.execute(text("SELECT count(*) FROM orders WHERE signal_id IN (SELECT entity_id FROM paper_effects WHERE job_run_id=:id AND effect_type='SIGNAL')"), {"id": run.job_run_id}).scalar_one() == 0
            assert outcome.reason_code == ("NO_SIGNAL" if path == "NO_TRADE" else "MAX_POSITION_NOTIONAL_EXCEEDED")

@pytest.mark.integration
@pytest.mark.parametrize("path", ["NO_TRADE", "FILLED"])
@pytest.mark.parametrize("commit_before_poll", [False, True])
@pytest.mark.parametrize("require_certified", [False, True])
def test_strict_service_authenticates_commit_and_repairs_missing_receipt_without_replay(certified_case, path, commit_before_poll, require_certified):
    from hope.application.market_data.calendar import MarketSessionCalendar
    from hope.infrastructure.paper_service import PaperServicePlan, run_service_cycle
    from hope.infrastructure.scheduling.paper import OperationalPaperJobBinding, OperationalPaperSchedule
    engine, make = certified_case
    data, run, strategy, work, _ = make(emit=path == "FILLED")
    # Match the scheduler-authenticated operational identity exactly.
    binding = OperationalPaperJobBinding(data["strategy_version_id"], data["market"], "service-certified", work)
    run = create_scheduled_job_run(binding.durable_job_key, data["as_of"])
    strategy.run = run
    factory = lambda *_: PaperServicePlan((binding,),
        (OperationalPaperSchedule(data["strategy_version_id"], data["market"], "service-certified", timedelta(0)),),
        {data["market"]: MarketSessionCalendar(((data["as_of"], data["as_of"]+timedelta(hours=1)),))},
        data["as_of"], data["as_of"]+timedelta(hours=1), timedelta(minutes=2))
    if commit_before_poll:
        # Real committed work without the service's subsequent audit step models
        # host loss between scheduler commit and successful-cycle authentication.
        registry = PaperJobRegistry([PaperJobDefinition(run.job_key, work)])
        assert run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1)) is PaperCycleOutcome.EXECUTED
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"), {"id": str(run.job_run_id)}).scalar_one() == 0
    assert run_service_cycle(engine, factory, migrations_dir=Path(__file__).parents[2]/"migrations",
        now=lambda: data["as_of"]+timedelta(minutes=1), require_certified=require_certified) == ((0, 1) if commit_before_poll else (1, 0))
    assert len(strategy.calls) == 1
    assert run_service_cycle(engine, factory, migrations_dir=Path(__file__).parents[2]/"migrations",
        now=lambda: data["as_of"]+timedelta(minutes=1), require_certified=require_certified) == (0, 1)
    assert len(strategy.calls) == 1
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"), {"id": str(run.job_run_id)}).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == (5 if path == "FILLED" else 1)


@pytest.mark.integration
def test_committed_demotion_between_service_plan_and_claim_blocks_certified_execution(certified_case, monkeypatch):
    from hope.application.market_data.calendar import MarketSessionCalendar
    from hope.infrastructure import paper_service as service
    from hope.infrastructure.scheduling.paper import OperationalPaperJobBinding, OperationalPaperSchedule
    engine, make = certified_case
    data, run, strategy, work, _ = make()
    binding = OperationalPaperJobBinding(data["strategy_version_id"], data["market"], "demotion-race", work)
    run = create_scheduled_job_run(binding.durable_job_key, data["as_of"])
    strategy.run = run
    original = service.run_due_operational_paper_jobs
    def demote_then_claim(*args, **kwargs):
        with engine.begin() as connection:
            SqlAlchemyStrategyCandidateRepository(connection).append(StrategyCandidateClassification(
                strategy_version_id=data["strategy_version_id"], markets=frozenset({data["market"]}),
                state=StrategyCandidateState.RESEARCH, rationale="Committed demotion after actual certified service registry admission"))
        return original(*args, **kwargs)
    monkeypatch.setattr(service, "run_due_operational_paper_jobs", demote_then_claim)
    factory = lambda *_: service.PaperServicePlan((binding,),
        (OperationalPaperSchedule(data["strategy_version_id"], data["market"], "demotion-race", timedelta(0)),),
        {data["market"]: MarketSessionCalendar(((data["as_of"], data["as_of"]+timedelta(hours=1)),))},
        data["as_of"], data["as_of"]+timedelta(hours=1), timedelta(minutes=2))
    with pytest.raises(IntegrityError, match="PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE"):
        service.run_service_cycle(engine, factory, migrations_dir=Path(__file__).parents[2]/"migrations",
            now=lambda: data["as_of"]+timedelta(minutes=1), require_certified=True)
    assert strategy.calls == []
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run) is None
        assert connection.execute(text("SELECT count(*) FROM paper_strategy_authorizations WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 0

@pytest.mark.integration
def test_hash_matching_generic_research_payload_is_rejected_before_claim(certified_case):
    engine, make = certified_case
    # Concurrent authoritative migration 231 now rejects this at promotion,
    # even earlier than the bridge's independent certified-source verification.
    with engine.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM job_runs")).scalar_one()
    with pytest.raises(IntegrityError, match="STRATEGY_CANDIDATE_CERTIFIED_CONTROL_EVIDENCE_REQUIRED"):
        make(certified=False)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM job_runs")).scalar_one() == before


@pytest.mark.integration
@pytest.mark.parametrize("failure,expected", [
    ("future_effective", "PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT"),
    ("stale", "PAPER_MARKET_DATA_STALE"),
    ("missing_quote", "PAPER_CERTIFIED_EXECUTABLE_QUOTE_UNAVAILABLE")])
def test_certified_paper_data_failure_rolls_back_authority_and_economic_effects(certified_case, failure, expected):
    from dataclasses import replace
    from hope.domain.market_data.models import MarketBar
    from hope.infrastructure.market_data.provider import MarketDataRequest
    from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
    from hope.infrastructure.repositories.market_data_manifest import SqlAlchemyMarketDataCoverageManifestRepository
    from hope.infrastructure.repositories.market_data_finalizer import SqlAlchemyMarketDataVersionFinalizer
    engine, make = certified_case
    data, run, strategy, work, registry = make(emit=True)
    paper_version = uuid4()
    event = data["as_of"] - timedelta(minutes=20 if failure == "stale" else 1)
    request = MarketDataRequest("TEST", ("ABC",), event, event + timedelta(minutes=1), timedelta(minutes=1))
    with engine.begin() as connection:
        dataset = connection.execute(text("SELECT dataset_id FROM dataset_versions WHERE dataset_version_id=:id"), {"id": data["dataset_version_id"]}).scalar_one()
        connection.execute(text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:id,:dataset,:version,'staging',FALSE)"), {"id": paper_version, "dataset": dataset, "version": f"negative-{paper_version}"})
        identities = {("TEST", "ABC"): data["instrument_id"]}
        SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(paper_version, data["universe_version_id"], (request,), identity_map=identities)
        bar = MarketBar(instrument_id=str(data["instrument_id"]), event_time=event, available_time=event, ingestion_time=event,
            effective_time=data["as_of"]+timedelta(hours=1) if failure == "future_effective" else None,
            open="100", high="101", low="99", close="100", volume="1000")
        SqlAlchemyMarketBarSink(connection).append(paper_version, (bar,))
        SqlAlchemyMarketDataVersionFinalizer(connection).finalize(paper_version, (request,), identity_map=identities)
    work = replace(work, dataset_version_id=paper_version)
    registry = PaperJobRegistry([PaperJobDefinition(run.job_key, work)])
    with pytest.raises(RuntimeError, match=expected):
        run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1))
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run).status.value == "FAILED"
        for table in ("paper_effects", "paper_strategy_authorizations", "paper_certified_outcomes"):
            assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"), {"id": data["configuration"]["paper_execution"]["portfolio_id"]}).scalar_one() == 0

@pytest.mark.integration
@pytest.mark.parametrize("path", ["NO_TRADE", "FILLED"])
def test_backend_death_after_bridge_effects_before_job_commit_rolls_back_entire_attempt(certified_case, path):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError
    engine, make = certified_case
    data, run, strategy, work, registry = make(emit=path == "FILLED")
    killed = []
    def kill_before_outcome(connection, cursor, statement, parameters, context, executemany):
        if "INSERT INTO paper_certified_outcomes" in statement and not killed:
            killed.append(True)
            pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            with engine.begin() as killer:
                assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}).scalar_one()
    event.listen(engine, "before_cursor_execute", kill_before_outcome)
    try:
        with pytest.raises(SQLAlchemyError):
            run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1))
    finally:
        event.remove(engine, "before_cursor_execute", kill_before_outcome)
    assert killed == [True]
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run) is None
        for table in ("paper_effects", "paper_strategy_authorizations", "paper_certified_outcomes"):
            assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"), {"id": data["configuration"]["paper_execution"]["portfolio_id"]}).scalar_one() == 0
    assert run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1)) is PaperCycleOutcome.EXECUTED
    assert run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1)) is PaperCycleOutcome.SKIPPED_TERMINAL
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == (5 if path == "FILLED" else 1)


@pytest.mark.integration
@pytest.mark.parametrize("path", ["NO_TRADE", "FILLED"])
def test_backend_death_during_postcommit_authentication_preserves_accounting_for_retry(certified_case, path):
    from hope.infrastructure.scheduling.paper_reconciliation import authenticate_successful_paper_run
    engine, make = certified_case
    data, run, strategy, work, registry = make(emit=path == "FILLED")
    assert run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1)) is PaperCycleOutcome.EXECUTED
    connection = engine.connect()
    transaction = connection.begin()
    pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
    try:
        authenticate_successful_paper_run(connection, run)
        with engine.begin() as killer:
            assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}).scalar_one()
        connection.invalidate()
    finally:
        transaction.rollback()
        connection.close()
    with engine.begin() as restarted:
        assert connection.closed
        assert SqlAlchemyJobRunRepository(restarted).get_record_for_run(run).status.value == "SUCCEEDED"
        assert restarted.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"), {"id": str(run.job_run_id)}).scalar_one() == 0
        authenticate_successful_paper_run(restarted, run)
        authenticate_successful_paper_run(restarted, run)
        assert restarted.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"), {"id": str(run.job_run_id)}).scalar_one() == 1
        if path == "FILLED":
            ledger = SqlAlchemyPaperPortfolioRepository(restarted).load_ledger(UUID(data["configuration"]["paper_execution"]["portfolio_id"]))
            assert ledger.state.cash == Decimal("799.75996")
            assert ledger.state.positions[data["instrument_id"]].quantity == Decimal("2")
    assert len(strategy.calls) == 1

@pytest.mark.integration
def test_certified_outcome_is_immutable_after_success(certified_case):
    engine, make = certified_case
    data, run, strategy, work, registry = make()
    assert run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1)) is PaperCycleOutcome.EXECUTED
    with engine.begin() as connection:
        for sql in ("UPDATE paper_certified_outcomes SET outcome='FILLED' WHERE job_run_id=:id", "DELETE FROM paper_certified_outcomes WHERE job_run_id=:id"):
            with pytest.raises(IntegrityError, match="IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(text(sql), {"id": run.job_run_id})
        assert connection.execute(text("SELECT outcome FROM paper_certified_outcomes WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == "NO_TRADE"


@pytest.mark.integration
def test_certified_outcome_rejects_wrong_source_and_contradictory_effect_shape(certified_case):
    from hope.application.jobs import JobRunStatus, create_job_run_completion
    from hope.application.paper.no_trade import PaperNoTradeWriter
    from hope.infrastructure.repositories.paper_no_trade import SqlAlchemyPaperNoTradeRepository
    from hope.infrastructure.repositories.paper_eligibility import SqlAlchemyPaperStrategyAuthorizationRepository
    engine, make = certified_case
    data, run, strategy, work, registry = make()
    with engine.begin() as connection:
        work.bind(connection)  # real certified eligibility and research proof
        jobs = SqlAlchemyJobRunRepository(connection)
        assert jobs.claim(run)
        assert SqlAlchemyPaperStrategyAuthorizationRepository(connection).record_for_run(run,
            data["strategy_version_id"], data["market"], data["classification_id"], data["research_decision_id"])
        assert PaperNoTradeWriter(SqlAlchemyPaperNoTradeRepository(connection)).record(PaperCycleContext(run),
            strategy_version="v1", decision_time=run.scheduled_for, inputs_hash="a"*64)
        values = dict(job=run.job_run_id, source=data["variant_run_id"], dataset=data["dataset_version_id"],
            universe=data["universe_version_id"], configuration=data["execution_plan"].configuration_hash,
            inputs="a"*64, time=run.scheduled_for-timedelta(minutes=1), outcome="NO_TRADE", reason="NO_SIGNAL")
        statement = text("INSERT INTO paper_certified_outcomes(job_run_id,source_run_id,dataset_version_id,universe_version_id,configuration_hash,inputs_hash,decision_time,outcome,reason_code) VALUES (:job,:source,:dataset,:universe,:configuration,:inputs,:time,:outcome,:reason)")
        dataset = connection.execute(text("SELECT dataset_id FROM dataset_versions WHERE dataset_version_id=:id"), {"id": data["dataset_version_id"]}).scalar_one()
        unsealed, missing_manifest = uuid4(), uuid4()
        connection.execute(text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:unsealed,:dataset,:unsealed_name,'staging',FALSE),(:missing,:dataset,:missing_name,'sealed',TRUE)"),
            {"unsealed": unsealed, "missing": missing_manifest, "dataset": dataset, "unsealed_name": f"unsealed-{unsealed}", "missing_name": f"missing-{missing_manifest}"})
        from hope.infrastructure.repositories.market_data_manifest import SqlAlchemyMarketDataCoverageManifestRepository
        SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(unsealed, data["universe_version_id"], (data["request"],), identity_map={("TEST", "ABC"): data["instrument_id"]})
        for bad, error in [({**values, "source": data["control_run_id"]}, "PROVENANCE_MISMATCH"),
                           ({**values, "outcome": "FILLED", "reason": "PORTFOLIO_RISK_APPROVED"}, "EFFECT_MISMATCH"),
                           ({**values, "inputs": "b"*64}, "INPUTS_MISMATCH"),
                           ({**values, "dataset": unsealed}, "DATASET_MISMATCH"),
                           ({**values, "dataset": missing_manifest}, "DATASET_MISMATCH")]:
            with pytest.raises(IntegrityError, match=error):
                with connection.begin_nested():
                    connection.execute(statement, bad)
        assert connection.execute(text("SELECT count(*) FROM paper_certified_outcomes WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 0
        connection.execute(statement, values)
        assert jobs.complete(create_job_run_completion(run, JobRunStatus.SUCCEEDED, data["as_of"]+timedelta(minutes=1)))

@pytest.mark.integration
@pytest.mark.parametrize("mismatch,reason", [
    ("family", "PAPER_CERTIFIED_IMPLEMENTATION_MISMATCH"),
    ("code_commit", "PAPER_CERTIFIED_IMPLEMENTATION_MISMATCH"),
    ("strategy_version", "PAPER_CERTIFIED_STRATEGY_IMPLEMENTATION_MISMATCH"),
    ("parameters", "PAPER_CERTIFIED_PARAMETERS_MISMATCH"),
    ("dataset_provider", "PAPER_CERTIFIED_DATASET_SOURCE_MISMATCH"),
    ("missing_dataset", "PAPER_CERTIFIED_DATASET_SOURCE_MISMATCH"),
    ("instrument_market", "PAPER_CERTIFIED_INSTRUMENT_MARKET_MISMATCH")])
def test_certified_identity_mismatch_cannot_write_economic_effects(certified_case, mismatch, reason):
    from dataclasses import replace
    engine, make = certified_case
    data, run, strategy, work, registry = make(instrument_market=StrategyMarket.INDIA if mismatch == "instrument_market" else None)
    if mismatch == "family":
        work = replace(work, definition=replace(work.definition, family="DIFFERENT_FAMILY"))
    elif mismatch == "code_commit":
        work = replace(work, definition=replace(work.definition, code_commit="different-reviewed-code"))
    elif mismatch == "strategy_version":
        class WrongVersion(_Decision):
            version = "v2"
        work = replace(work, definition=replace(work.definition, strategy_factory=lambda _: WrongVersion(run, False)))
    elif mismatch == "parameters":
        class WrongParameters(ParameterSnapshot):
            lookback: int
        work = replace(work, definition=replace(work.definition, parameters_factory=lambda _: WrongParameters(lookback=1)))
    elif mismatch == "missing_dataset":
        work = replace(work, dataset_version_id=uuid4())
    elif mismatch == "dataset_provider":
        dataset, version = uuid4(), uuid4()
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO datasets(dataset_id,name,source,pit_certified) VALUES (:id,:name,'OTHER_PROVIDER',TRUE)"), {"id": dataset, "name": f"M2-OTHER-{dataset}"})
            connection.execute(text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:id,:dataset,'v1','staging',FALSE)"), {"id": version, "dataset": dataset})
        work = replace(work, dataset_version_id=version)
    registry = PaperJobRegistry([PaperJobDefinition(run.job_key, work)])
    with pytest.raises(RuntimeError, match=reason):
        run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1))
    with engine.connect() as connection:
        durable = SqlAlchemyJobRunRepository(connection).get_record_for_run(run)
        assert (durable is not None and durable.status.value == "FAILED") if mismatch == "instrument_market" else durable is None
        for table in ("paper_strategy_authorizations", "paper_certified_outcomes", "paper_effects"):
            assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 0
    assert strategy.calls == []


@pytest.mark.integration
@pytest.mark.parametrize("mixed", [False, True])
def test_strict_service_refuses_generic_callable_before_claim(certified_case, mixed):
    from hope.application.market_data.calendar import MarketSessionCalendar
    from hope.infrastructure.paper_service import PaperServicePlan, run_service_cycle
    from hope.infrastructure.scheduling.paper import OperationalPaperJobBinding, OperationalPaperSchedule
    engine, make = certified_case
    data, run, strategy, work, registry = make()
    binding = OperationalPaperJobBinding(data["strategy_version_id"], data["market"], "generic-denied", lambda _: None)
    run = create_scheduled_job_run(binding.durable_job_key, data["as_of"])
    bindings = (binding, OperationalPaperJobBinding(data["strategy_version_id"], data["market"], "mixed", work)) if mixed else (binding,)
    factory = lambda *_: PaperServicePlan(bindings,
        (OperationalPaperSchedule(data["strategy_version_id"], data["market"], "generic-denied", timedelta(0)),),
        {data["market"]: MarketSessionCalendar(((data["as_of"], data["as_of"]+timedelta(hours=1)),))},
        data["as_of"], data["as_of"]+timedelta(hours=1), timedelta(minutes=2))
    with pytest.raises(RuntimeError, match="PAPER_SERVICE_CERTIFIED_BINDING_REQUIRED"):
        run_service_cycle(engine, factory, migrations_dir=Path(__file__).parents[2]/"migrations",
            now=lambda: data["as_of"]+timedelta(minutes=1), require_certified=not mixed)
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run) is None
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 0
    assert strategy.calls == []
