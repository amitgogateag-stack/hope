"""Recovered sealed feeds retain PIT cutoffs through PAPER execution/restart."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, local
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from test_certified_paper_bridge import certified_case
from hope.application.paper import PaperCycleOutcome
from hope.domain.market_data.models import MarketBar
from hope.infrastructure.market_data.provider import MarketDataRequest
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry, run_paper_once
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from hope.infrastructure.repositories.market_data_finalizer import SqlAlchemyMarketDataVersionFinalizer
from hope.infrastructure.repositories.market_data_manifest import SqlAlchemyMarketDataCoverageManifestRepository


def _recovered_feed(engine, data, work, *, phase="decision", field=None,
                    late=False, stale=False):
    """Publish a new immutable same-source vintage; never mutate research data."""
    decision = data["as_of"] - timedelta(minutes=1)
    event = decision - timedelta(seconds=120, microseconds=int(stale))
    bars = []
    for name, timestamp, cutoff in (("decision", event, decision),
                                     ("fill", data["as_of"], data["as_of"])):
        times = dict(available_time=cutoff, ingestion_time=cutoff, effective_time=cutoff)
        if phase == name and field is not None:
            times[field] += timedelta(microseconds=int(late))
            if field == "available_time":
                times["ingestion_time"] = times[field]
        bars.append(MarketBar(instrument_id=str(data["instrument_id"]), event_time=timestamp,
            **times, open="100", high="101", low="99", close="100", volume="1000"))
    requests = tuple(MarketDataRequest("TEST", ("ABC",), bar.event_time,
        bar.event_time + timedelta(minutes=1), timedelta(minutes=1)) for bar in bars)
    version = uuid4()
    identities = {("TEST", "ABC"): data["instrument_id"]}
    with engine.begin() as connection:
        dataset = connection.execute(text("SELECT dataset_id FROM dataset_versions WHERE dataset_version_id=:id"),
            {"id": data["dataset_version_id"]}).scalar_one()
        connection.execute(text("INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,vintage_label,immutable) VALUES (:id,:dataset,:version,'staging',FALSE)"),
            {"id": version, "dataset": dataset, "version": f"recovered-timing-{version}"})
        SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(version,
            data["universe_version_id"], requests, identity_map=identities)
        SqlAlchemyMarketBarSink(connection).append(version, tuple(bars))
        SqlAlchemyMarketDataVersionFinalizer(connection).finalize(version, requests, identity_map=identities)
    updated = replace(work, dataset_version_id=version)
    return updated, decision


def _registry(run, work):
    return PaperJobRegistry([PaperJobDefinition(run.job_key, work)])


def _snapshot(engine, data, run):
    """Observe exact durable identities/payloads, not only counts."""
    with engine.connect() as connection:
        result = {}
        for table in ("paper_effects", "paper_strategy_authorizations", "paper_certified_outcomes"):
            result[table] = connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t WHERE job_run_id=:id ORDER BY to_jsonb(t)::text"),
                {"id": run.job_run_id}).scalars().all()
        result["portfolio"] = connection.execute(text("SELECT to_jsonb(t) FROM paper_portfolios t WHERE portfolio_id=:id"),
            {"id": data["configuration"]["paper_execution"]["portfolio_id"]}).scalars().all()
        result["job"] = connection.execute(text("SELECT status,failure_code FROM job_runs WHERE job_run_id=:id"),
            {"id": run.job_run_id}).one()
        return result


@pytest.mark.integration
@pytest.mark.parametrize("phase", ["decision", "fill"])
@pytest.mark.parametrize("field", ["available_time", "ingestion_time", "effective_time"])
@pytest.mark.parametrize("late", [False, True])
def test_recovered_feed_visibility_cutoffs_and_terminal_restart(certified_case, phase, field, late):
    engine, make = certified_case
    data, run, strategy, work, _ = make(emit=True)
    work, decision = _recovered_feed(engine, data, work, phase=phase, field=field, late=late)
    registry = _registry(run, work)
    clock = lambda: data["as_of"] + timedelta(minutes=1)
    if late:
        expected = ("PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT" if phase == "decision"
                    else "PAPER_CERTIFIED_EXECUTABLE_QUOTE_UNAVAILABLE")
        with pytest.raises(RuntimeError, match=expected):
            run_paper_once(engine, run, registry, now=clock)
    else:
        assert run_paper_once(engine, run, registry, now=clock) is PaperCycleOutcome.EXECUTED
    before = _snapshot(engine, data, run)
    assert before["job"].status == ("FAILED" if late else "SUCCEEDED")
    assert len(before["paper_effects"]) == (0 if late else 5)
    assert len(before["portfolio"]) == (0 if late else 1)
    if late:
        assert before["paper_strategy_authorizations"] == before["paper_certified_outcomes"] == []
    else:
        assert strategy.calls[0].as_of == decision
        assert len(strategy.calls[0].bars) == 1
        assert strategy.calls[0].bars[0].event_time == decision - timedelta(seconds=120)
        assert before["paper_certified_outcomes"][0]["outcome"] == "FILLED"
    calls = len(strategy.calls)
    # A later poll must not reinterpret a past decision using newly visible data.
    assert run_paper_once(engine, run, registry,
        now=lambda: data["as_of"] + timedelta(hours=1)) is PaperCycleOutcome.SKIPPED_TERMINAL
    assert len(strategy.calls) == calls
    assert _snapshot(engine, data, run) == before


@pytest.mark.integration
@pytest.mark.parametrize("emit", [False, True])
@pytest.mark.parametrize("stale", [False, True])
def test_recovered_feed_exact_freshness_boundary_precedes_strategy(certified_case, emit, stale):
    engine, make = certified_case
    data, run, strategy, work, _ = make(emit=emit)
    work, _ = _recovered_feed(engine, data, work, stale=stale)
    if stale:
        with pytest.raises(RuntimeError, match="PAPER_MARKET_DATA_STALE"):
            run_paper_once(engine, run, _registry(run, work), now=lambda: data["as_of"] + timedelta(minutes=1))
        assert strategy.calls == []
        durable = _snapshot(engine, data, run)
        assert durable["paper_effects"] == durable["paper_certified_outcomes"] == durable["portfolio"] == []
    else:
        assert run_paper_once(engine, run, _registry(run, work),
            now=lambda: data["as_of"] + timedelta(minutes=1)) is PaperCycleOutcome.EXECUTED
        assert _snapshot(engine, data, run)["paper_certified_outcomes"][0]["outcome"] == ("FILLED" if emit else "NO_TRADE")


@pytest.mark.integration
@pytest.mark.parametrize("field", ["available_time", "ingestion_time", "effective_time"])
def test_no_trade_does_not_require_a_future_execution_quote(certified_case, field):
    engine, make = certified_case
    data, run, strategy, work, _ = make(emit=False)
    work, _ = _recovered_feed(engine, data, work, phase="fill", field=field, late=True)
    assert run_paper_once(engine, run, _registry(run, work),
        now=lambda: data["as_of"] + timedelta(minutes=1)) is PaperCycleOutcome.EXECUTED
    durable = _snapshot(engine, data, run)
    assert durable["paper_certified_outcomes"][0]["outcome"] == "NO_TRADE"
    assert len(durable["paper_effects"]) == 1
    assert durable["portfolio"] == []


@pytest.mark.integration
@pytest.mark.parametrize("emit", [False, True])
def test_overlapping_workers_commit_one_recovered_feed_decision(certified_case, emit):
    engine, make = certified_case
    data, run, strategy, work, _ = make(emit=emit)
    work, _ = _recovered_feed(engine, data, work)
    registry = _registry(run, work)
    entered, release, contender = Event(), Event(), Event()
    worker, pids = local(), {}
    def hold_before_outcome(connection, cursor, statement, parameters, context, executemany):
        if getattr(worker, "second", False):
            pids["second"] = cursor.connection.info.backend_pid
            contender.set()
        if "INSERT INTO paper_certified_outcomes" in statement:
            pids["first"] = cursor.connection.info.backend_pid
            entered.set()
            assert release.wait(8), "Test failed to release the first transaction"
    def second():
        worker.second = True
        return run_paper_once(engine, run, registry, now=lambda: data["as_of"] + timedelta(minutes=1))
    event.listen(engine, "before_cursor_execute", hold_before_outcome)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(run_paper_once, engine, run, registry,
                now=lambda: data["as_of"] + timedelta(minutes=1))
            try:
                assert entered.wait(8), "First worker never reached its uncommitted outcome"
                other = workers.submit(second)
                assert contender.wait(3)
                # The first transaction remains uncommitted while a second
                # independent PostgreSQL connection starts the same durable run.
                with engine.connect() as observer:
                    assert observer.execute(text("SELECT count(*) FROM job_runs WHERE job_run_id=:id"),
                        {"id": run.job_run_id}).scalar_one() == 0
                    deadline = monotonic() + 4
                    blockers = []
                    while monotonic() < deadline:
                        blockers = observer.execute(text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": pids["second"]}).scalar_one()
                        if pids["first"] in blockers:
                            break
                        sleep(0.02)
                    assert pids["first"] in blockers, "Contender did not reach the actual database lock"
            finally:
                release.set()
            assert first.result(timeout=12) is PaperCycleOutcome.EXECUTED
            assert other.result(timeout=12) is PaperCycleOutcome.SKIPPED_TERMINAL
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", hold_before_outcome)
    assert len(strategy.calls) == 1
    durable = _snapshot(engine, data, run)
    assert durable["job"].status == "SUCCEEDED"
    assert len(durable["paper_effects"]) == (5 if emit else 1)
    assert len(durable["paper_certified_outcomes"]) == len(durable["paper_strategy_authorizations"]) == 1
    assert len(durable["portfolio"]) == int(emit)


@pytest.mark.integration
@pytest.mark.parametrize("emit", [False, True])
def test_backend_loss_rolls_back_then_reuses_identical_recovered_decision_inputs(certified_case, emit):
    engine, make = certified_case
    data, run, strategy, work, _ = make(emit=emit)
    work, _ = _recovered_feed(engine, data, work)
    registry = _registry(run, work)
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
            run_paper_once(engine, run, registry, now=lambda: data["as_of"] + timedelta(minutes=1))
    finally:
        event.remove(engine, "before_cursor_execute", kill_before_outcome)
    assert killed == [True]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM job_runs WHERE job_run_id=:id"),
            {"id": run.job_run_id}).scalar_one() == 0
        for table in ("paper_effects", "paper_strategy_authorizations", "paper_certified_outcomes"):
            assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE job_run_id=:id"),
                {"id": run.job_run_id}).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"),
            {"id": data["configuration"]["paper_execution"]["portfolio_id"]}).scalar_one() == 0
    assert run_paper_once(engine, run, registry,
        now=lambda: data["as_of"] + timedelta(minutes=1)) is PaperCycleOutcome.EXECUTED
    assert len(strategy.calls) == 2
    assert strategy.calls[0] == strategy.calls[1]
    before = _snapshot(engine, data, run)
    assert len(before["paper_effects"]) == (5 if emit else 1)
    assert run_paper_once(engine, run, registry,
        now=lambda: data["as_of"] + timedelta(minutes=1)) is PaperCycleOutcome.SKIPPED_TERMINAL
    assert _snapshot(engine, data, run) == before
