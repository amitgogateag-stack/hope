"""Recovered DST data remains PIT-safe at the durable PAPER boundary."""
import os
from copy import copy
import runpy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper import PaperCycleOutcome
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.lifecycle import ingest_and_seal_market_data_windows
from hope.infrastructure.market_data.provider import MarketDataRequest, ProviderMarketDataBatch
from hope.infrastructure.market_data.recovery import ingest_market_data_recovery_windows
from hope.infrastructure.paper_runtime import DurableUniversePaperStrategyDecision, PaperMarketDataFreshnessPolicy, PaperJobDefinition, PaperJobRegistry, run_paper_once
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from hope.infrastructure.repositories.market_contexts import PITMarketContextRepository
from hope.infrastructure.repositories.market_data_finalizer import SqlAlchemyMarketDataVersionFinalizer
from hope.infrastructure.repositories.market_data_recovery import SqlAlchemyMarketDataRecoveryAuthorizer

NY = ZoneInfo("America/New_York")
_seed = runpy.run_path(str(Path(__file__).with_name("test_market_data_timezone_integrity.py")))["_seed"]


class _DelayedFoldProvider:
    source = "TEST"

    def fetch_bars(self, request):
        rows = []
        cursor = request.start.astimezone(UTC)
        while cursor < request.end.astimezone(UTC):
            event = cursor.astimezone(NY)
            available = (cursor + timedelta(minutes=45)).astimezone(NY) if event.hour == 1 and event.fold else event
            rows.append(RawMarketBar("TEST", "ABC", event, available, available,
                "100", "101", "99", "100", "1000"))
            cursor += request.interval
        return ProviderMarketDataBatch("TEST", request, tuple(rows), request.end)


class _ObserveNoTrade(Strategy):
    name = "recovered-timing-no-trade"
    version = "v1"

    def __init__(self):
        self.calls = []

    def generate_signals(self, market_context, universe, universe_members, parameters, inputs_hash):
        self.calls.append(market_context)
        return ()


@pytest.fixture
def recovered_data():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c timezone=America/New_York"})
    request = MarketDataRequest("TEST", ("ABC",), datetime(2025, 11, 2, 0, tzinfo=NY),
        datetime(2025, 11, 2, 3, tzinfo=NY), timedelta(hours=1))
    with engine.begin() as connection:
        apply_migrations(connection, Path(__file__).parents[2] / "migrations")
        version, identities = _seed(connection, request)
        universe = connection.execute(text("SELECT universe_version_id FROM market_data_coverage_manifests WHERE dataset_version_id=:id"), {"id": version}).scalar_one()
        strategy_id, strategy_version = uuid4(), uuid4()
        connection.execute(text("INSERT INTO strategies(strategy_id,name,family) VALUES (:id,:name,'TEST')"), {"id": strategy_id, "name": f"TIMING-{strategy_id}"})
        connection.execute(text("INSERT INTO strategy_versions(strategy_version_id,strategy_id,version,code_commit) VALUES (:id,:strategy,'v1','recovered-timing-test')"), {"id": strategy_version, "strategy": strategy_id})
    # Recovery commits independently, and a restarted connection retries before
    # the full manifest-backed lifecycle proves coverage and seals the dataset.
    for _ in range(2):
        with engine.begin() as connection:
            ingest_market_data_recovery_windows(_DelayedFoldProvider(), SqlAlchemyMarketBarSink(connection),
                SqlAlchemyMarketDataRecoveryAuthorizer(connection), version, (request,), identity_map=identities)
    with engine.begin() as connection:
        ingest_and_seal_market_data_windows(_DelayedFoldProvider(), SqlAlchemyMarketBarSink(connection),
            SqlAlchemyMarketDataVersionFinalizer(connection), version, (request,), identity_map=identities)
        assert connection.execute(text("SELECT count(*) FROM market_bars WHERE dataset_version_id=:id"), {"id": version}).scalar_one() == 4
    try:
        yield engine, version, universe, tuple(identities.values()), strategy_version
    finally:
        engine.dispose()


def _registry(data, as_of, max_age):
    engine, version, universe, instruments, strategy_version = data
    strategy = _ObserveNoTrade()
    run = create_scheduled_job_run(f"paper:USA:{strategy_version}:timing-{uuid4()}", as_of)
    job = DurableUniversePaperStrategyDecision(strategy, version, as_of, universe,
        ParameterSnapshot(), PaperMarketDataFreshnessPolicy(max_age))
    return run, strategy, PaperJobRegistry([PaperJobDefinition(run.job_key, job)])


@pytest.mark.integration
def test_recovered_first_fold_is_stale_in_second_fold_before_new_bar_available(recovered_data):
    as_of = datetime(2025, 11, 2, 1, 30, tzinfo=NY, fold=1)
    run, strategy, registry = _registry(recovered_data, as_of, timedelta(minutes=45))
    engine, version, universe, instruments, _ = recovered_data
    with engine.connect() as connection:
        context = PITMarketContextRepository(connection).get(version, as_of=as_of,
            universe_version_id=universe, instrument_ids=instruments)
        assert [bar.event_time.astimezone(UTC) for bar in context.bars] == [
            datetime(2025, 11, 2, 4, tzinfo=UTC), datetime(2025, 11, 2, 5, tzinfo=UTC)]
    with pytest.raises(RuntimeError, match="PAPER_MARKET_DATA_STALE"):
        run_paper_once(engine, run, registry, now=lambda: as_of.astimezone(UTC) + timedelta(minutes=1))
    assert strategy.calls == []
    with engine.connect() as connection:
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run) is None
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 0


@pytest.mark.integration
@pytest.mark.parametrize("concurrent", [False, True])
def test_recovered_second_fold_decision_is_visible_once_and_restart_skips(recovered_data, concurrent):
    as_of = datetime(2025, 11, 2, 1, 50, tzinfo=NY, fold=1)
    run, strategy, registry = _registry(recovered_data, as_of, timedelta(minutes=55))
    engine = recovered_data[0]
    if concurrent:
        barrier = Barrier(2)
        def execute():
            barrier.wait(timeout=5)
            return run_paper_once(engine, run, registry, now=lambda: as_of.astimezone(UTC) + timedelta(minutes=1))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(execute) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        assert results.count(PaperCycleOutcome.EXECUTED) == 1
        assert results.count(PaperCycleOutcome.SKIPPED_TERMINAL) == 1
    else:
        assert run_paper_once(engine, run, registry, now=lambda: as_of.astimezone(UTC) + timedelta(minutes=1)) is PaperCycleOutcome.EXECUTED
    assert len(strategy.calls) == 1
    assert [bar.event_time.astimezone(UTC) for bar in strategy.calls[0].bars] == [
        datetime(2025, 11, 2, 4, tzinfo=UTC), datetime(2025, 11, 2, 5, tzinfo=UTC), datetime(2025, 11, 2, 6, tzinfo=UTC)]
    assert run_paper_once(engine, run, registry, now=lambda: as_of.astimezone(UTC) + timedelta(minutes=2)) is PaperCycleOutcome.SKIPPED_TERMINAL
    assert len(strategy.calls) == 1
    with engine.connect() as connection:
        # Present the same durable ID with a genuinely different instant, while
        # keeping wall-clock text equal. Authentication must still reject it.
        forged = copy(run)
        object.__setattr__(forged, "scheduled_for", datetime(2025, 11, 2, 1, 50, tzinfo=NY, fold=0))
        with pytest.raises(ValueError, match="JOB_RUN_IDENTITY_CONFLICT"):
            SqlAlchemyJobRunRepository(connection).get_record_for_run(forged)
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run).status.value == "SUCCEEDED"
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM paper_no_trade_decisions WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == 1


