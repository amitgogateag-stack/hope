"""Historical recovery uses real immutable research and PostgreSQL accounting."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_certified_paper_bridge import certified_case
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.runner import PaperRuntimeContext
from hope.domain.strategy.candidates import StrategyCandidateClassification, StrategyCandidateState
from hope.infrastructure.paper_runtime import SqlAlchemyPaperRuntime, run_paper_once
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository
from hope.infrastructure.repositories.strategy_candidates import SqlAlchemyStrategyCandidateRepository
from hope.infrastructure.scheduling import certified_paper_recovery as recovery
from hope.infrastructure.paper_runtime import PaperJobDefinition, PaperJobRegistry


def _commit(engine, data, run, work, registry, claimed):
    if not claimed:
        run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1))
        return
    with engine.begin() as connection:
        execute = work.bind(connection)
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        runtime = SqlAlchemyPaperRuntime(connection, now=lambda: data["as_of"]+timedelta(minutes=1))
        execute(PaperRuntimeContext(PaperCycleContext(run), runtime._signal_writer, runtime._risk_writer,
            runtime._order_writer, runtime._fill_writer, runtime._terminal_writer, runtime._no_trade_writer,
            SqlAlchemyPaperEnvironmentControlRepository(connection).assert_running))


def _revoke(engine, data, which):
    with engine.begin() as connection:
        if which == "demotion":
            SqlAlchemyStrategyCandidateRepository(connection).append(StrategyCandidateClassification(
                strategy_version_id=data["strategy_version_id"], markets=frozenset({data["market"]}),
                state=StrategyCandidateState.RESEARCH, rationale="Controlled historical recovery demotion"))
        else:
            connection.execute(text("INSERT INTO experiment_invalidations(experiment_id,reason) VALUES (:id,'Controlled historical recovery revocation')"),
                {"id": data[f"{which}_experiment_id"]})


def _audit(connection, run):
    return connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
        {"id": str(run.job_run_id)}).scalar_one()


@pytest.mark.integration
@pytest.mark.parametrize("path", ["NO_TRADE", "RISK_BLOCKED", "FILLED"])
@pytest.mark.parametrize("revocation", ["demotion", "control", "variant"])
@pytest.mark.parametrize("claimed", [False, True])
def test_revoked_historical_run_recovers_once_without_reauthorizing(certified_case, path, revocation, claimed):
    engine, make = certified_case
    data, run, strategy, work, registry = make(emit=path != "NO_TRADE", blocked=path == "RISK_BLOCKED")
    _commit(engine, data, run, work, registry, claimed)
    _revoke(engine, data, revocation)
    with engine.begin() as connection:
        assert _audit(connection, run) == 0
        result = recovery.recover_certified_paper_batch(connection, (run.job_run_id,), current=data["as_of"]+timedelta(days=365))
        assert result.reconciled_run_ids == ((run.job_run_id,) if claimed else ())
        assert result.authenticated_run_ids == (() if claimed else (run.job_run_id,))
        assert _audit(connection, run) == 1
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(run).status.value == "SUCCEEDED"
    with engine.begin() as connection:
        recovery.recover_certified_paper_batch(connection, (run.job_run_id,), current=data["as_of"]+timedelta(days=366))
        assert _audit(connection, run) == 1
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"), {"id": run.job_run_id}).scalar_one() == (5 if path == "FILLED" else 1)
    assert len(strategy.calls) == 1
    # The historical receipt never authorizes a new execution after revocation.
    with engine.begin() as connection:
        with pytest.raises(Exception, match="AUTHORIZATION_(INELIGIBLE|RESEARCH_INVALID)"):
            with connection.begin_nested():
                work.bind(connection)


@pytest.mark.integration
@pytest.mark.parametrize("partial", [False, True])
def test_missing_or_incomplete_run_aborts_whole_selection(certified_case, partial):
    engine, make = certified_case
    data, run, strategy, work, registry = make()
    _commit(engine, data, run, work, registry, False)
    bad = uuid4()
    if partial:
        other, bad_run, _, bad_work, _ = make()
        bad = bad_run.job_run_id
        with engine.begin() as connection:
            bad_work.bind(connection)
            assert SqlAlchemyJobRunRepository(connection).claim(bad_run)
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="COMMITTED_RUN_REQUIRED|RECEIPT_MISMATCH"):
            recovery.recover_certified_paper_batch(connection, (run.job_run_id,bad), current=data["as_of"]+timedelta(days=1))
        assert _audit(connection, run) == 0
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


@pytest.mark.integration
def test_later_authentication_failure_rolls_back_earlier_audit(certified_case, monkeypatch):
    engine, make = certified_case
    cases = [make(), make()]
    for data, run, _, work, registry in cases:
        _commit(engine, data, run, work, registry, False)
    original, calls = recovery.authenticate_successful_paper_run, []
    def fail_second(connection, run):
        calls.append(run.job_run_id)
        if len(calls) == 2:
            raise RuntimeError("Controlled second receipt failure")
        original(connection, run)
    monkeypatch.setattr(recovery, "authenticate_successful_paper_run", fail_second)
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="Controlled second"):
            recovery.recover_certified_paper_batch(connection, (case[1].job_run_id for case in cases), current=cases[0][0]["as_of"]+timedelta(days=1))
        assert len(calls) == 2
        assert all(_audit(connection, case[1]) == 0 for case in cases)


@pytest.mark.integration
def test_competing_reverse_selections_authenticate_once(certified_case):
    engine, make = certified_case
    cases = [make(emit=True), make()]
    for data, run, _, work, registry in cases:
        _commit(engine, data, run, work, registry, True)
    ids = tuple(case[1].job_run_id for case in cases)
    barrier = Barrier(2)
    def worker(selected):
        barrier.wait(timeout=5)
        with engine.begin() as connection:
            return recovery.recover_certified_paper_batch(connection, selected, current=cases[0][0]["as_of"]+timedelta(days=1))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, ids), pool.submit(worker, tuple(reversed(ids)))]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(len(result.reconciled_run_ids) for result in results) == [0,2]
    with engine.connect() as connection:
        assert all(_audit(connection, case[1]) == 1 for case in cases)


@pytest.mark.integration
def test_backend_loss_after_receipt_before_commit_is_retryable(certified_case):
    engine, make = certified_case
    data, run, strategy, work, registry = make(emit=True)
    _commit(engine, data, run, work, registry, False)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        recovery.recover_certified_paper_batch(connection, (run.job_run_id,), current=data["as_of"]+timedelta(days=1))
        with engine.begin() as killer:
            assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid":pid}).scalar_one()
        connection.invalidate()
    finally:
        transaction.rollback()
        connection.close()
    with engine.begin() as restarted:
        assert _audit(restarted, run) == 0
        recovery.recover_certified_paper_batch(restarted, (run.job_run_id,), current=data["as_of"]+timedelta(days=1))
        assert _audit(restarted, run) == 1
    assert len(strategy.calls) == 1


@pytest.mark.integration
def test_real_cli_recovers_demoted_fill_and_retries_without_factory(certified_case):
    engine, make = certified_case
    data, run, strategy, work, registry = make(emit=True)
    _commit(engine, data, run, work, registry, False)
    _revoke(engine, data, "demotion")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    command = [sys.executable,"-m","hope.api.paper_recovery","--run-id",str(run.job_run_id),"--migrations-dir",str(Path(__file__).parents[2]/"migrations")]
    for _ in range(2):
        result = subprocess.run(command, env=env, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr.decode()
        value = json.loads(result.stdout)
        assert value["status"] == "VERIFIED" and value["live_enabled"] is False
        assert value["authenticated_run_ids"] == [str(run.job_run_id)]
    with engine.connect() as connection:
        assert _audit(connection, run) == 1
    assert len(strategy.calls) == 1


@pytest.mark.integration
def test_generic_success_cannot_be_labelled_certified_recovery(certified_case):
    engine, make = certified_case
    data, run, _, _, _ = make()
    def generic(runtime):
        runtime.record_no_trade(strategy_version="v1", decision_time=run.scheduled_for, inputs_hash="c"*64)
    run_paper_once(engine, run, PaperJobRegistry([PaperJobDefinition(run.job_key,generic)]),
        now=lambda: data["as_of"]+timedelta(minutes=1))
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="RECEIPT_MISMATCH"):
            recovery.recover_certified_paper_batch(connection, (run.job_run_id,), current=data["as_of"]+timedelta(days=1))
        assert _audit(connection, run) == 0


@pytest.mark.integration
def test_halted_control_blocks_recovery_without_resuming_it(certified_case):
    engine, make = certified_case
    data, run, _, work, registry = make()
    _commit(engine, data, run, work, registry, False)
    with engine.begin() as connection:
        SqlAlchemyPaperEnvironmentControlRepository(connection).transition("RUNNING","HALTED","Controlled historical recovery halt",actor="integration-test")
    try:
        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="PAPER_ENVIRONMENT_HALTED"):
                recovery.recover_certified_paper_batch(connection, (run.job_run_id,), current=data["as_of"]+timedelta(days=1))
            assert _audit(connection, run) == 0
            assert SqlAlchemyPaperEnvironmentControlRepository(connection).current_state() == "HALTED"
    finally:
        with engine.begin() as connection:
            SqlAlchemyPaperEnvironmentControlRepository(connection).transition("HALTED","RUNNING","Controlled historical recovery cleanup",actor="integration-test")


@pytest.mark.integration
@pytest.mark.parametrize("isolation", ["REPEATABLE READ","SERIALIZABLE"])
def test_stale_transaction_isolation_cannot_recover(certified_case, isolation):
    engine, make = certified_case
    data, run, _, work, registry = make()
    _commit(engine, data, run, work, registry, False)
    with engine.connect().execution_options(isolation_level=isolation) as connection:
        with pytest.raises(RuntimeError, match="PAPER_ENVIRONMENT_CONTROL_REQUIRES_READ_COMMITTED"):
            recovery.recover_certified_paper_batch(connection, (run.job_run_id,), current=data["as_of"]+timedelta(days=1))
    with engine.connect() as connection:
        assert _audit(connection, run) == 0
