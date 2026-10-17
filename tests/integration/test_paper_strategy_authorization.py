"""Real promotion/demotion, claim and immutable authorization concurrency proofs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import runpy
from threading import Barrier, Event
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.application.paper.context import PaperCycleContext
from hope.application.paper.no_trade import PaperNoTradeWriter
from hope.domain.strategy.candidates import StrategyCandidateClassification, StrategyCandidateState, StrategyMarket
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_eligibility import assert_current_eligibility, SqlAlchemyPaperStrategyAuthorizationRepository
from hope.infrastructure.repositories.paper_no_trade import SqlAlchemyPaperNoTradeRepository
from hope.infrastructure.repositories.strategy_candidates import SqlAlchemyStrategyCandidateRepository
from hope.infrastructure.scheduling.paper_reconciliation import reconcile_completed_paper_run

_seed = runpy.run_path(str(Path(__file__).with_name("m2_research_fixture.py")))["seed"]


@pytest.fixture
def eligible():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")
    engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=10000"})
    with engine.begin() as connection:
        apply_migrations(connection, Path(__file__).parents[2] / "migrations")
        data = _seed(connection)
    data["run"] = create_scheduled_job_run(f"paper:USA:{data['strategy_version_id']}:auth-{uuid4()}", data["as_of"])
    try:
        yield engine, data
    finally:
        with engine.begin() as connection:
            connection.execute(text("UPDATE job_runs SET status='FAILED',completed_at=clock_timestamp(),failure_code='EXPECTED_M2_AUTH_TEST_CLEANUP' WHERE job_key LIKE :prefix AND status='CLAIMED'"), {"prefix": f"paper:USA:{data['strategy_version_id']}:%"})
            _demote(connection, data)
        engine.dispose()


def _demote(connection, data):
    SqlAlchemyStrategyCandidateRepository(connection).append(StrategyCandidateClassification(
        strategy_version_id=data["strategy_version_id"], markets=frozenset({StrategyMarket.USA}),
        state=StrategyCandidateState.RESEARCH, rationale=f"Synthetic authorization revocation {uuid4()}"))


def _assert(connection, data):
    assert_current_eligibility(connection, data["strategy_version_id"], StrategyMarket.USA,
        data["classification_id"], data["research_decision_id"])


def _record(connection, data):
    return SqlAlchemyPaperStrategyAuthorizationRepository(connection).record_for_run(data["run"],
        data["strategy_version_id"], StrategyMarket.USA, data["classification_id"], data["research_decision_id"])


def _complete(connection, data):
    _assert(connection, data)
    repository = SqlAlchemyJobRunRepository(connection)
    if not repository.claim(data["run"]):
        return False
    assert _record(connection, data)
    assert PaperNoTradeWriter(SqlAlchemyPaperNoTradeRepository(connection)).record(PaperCycleContext(data["run"]),
        strategy_version="v1", decision_time=data["as_of"], inputs_hash="a" * 64)
    assert repository.complete(create_job_run_completion(data["run"], JobRunStatus.SUCCEEDED, data["as_of"] + timedelta(minutes=1)))
    return True


def _counts(engine, data):
    with engine.connect() as connection:
        return tuple(connection.execute(text(f"SELECT count(*) FROM {table} WHERE job_run_id=:id"),
            {"id": data["run"].job_run_id}).scalar_one() for table in ("job_runs", "paper_strategy_authorizations", "paper_effects"))


def _wait_for_block(engine, pid):
    deadline = time.monotonic() + 5
    with engine.connect() as connection:
        while not connection.execute(text("SELECT cardinality(pg_blocking_pids(:pid))>0"), {"pid": pid}).scalar_one():
            assert time.monotonic() < deadline, "Expected authorization/candidate lock wait"
            time.sleep(0.01)


@pytest.mark.integration
def test_committed_demotion_before_new_claim_is_rejected(eligible):
    engine, data = eligible
    with engine.begin() as connection:
        _demote(connection, data)
    with engine.begin() as connection:
        with pytest.raises(IntegrityError, match="AUTHORIZATION_INELIGIBLE"):
            with connection.begin_nested():
                _complete(connection, data)
    assert _counts(engine, data) == (0, 0, 0)


@pytest.mark.integration
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_repeatable_snapshot_cannot_authorize_after_demotion(eligible, isolation):
    engine, data = eligible
    with engine.connect() as stale:
        transaction = stale.begin()
        stale.execute(text(f"SET TRANSACTION ISOLATION LEVEL {isolation}"))
        assert stale.execute(text("SELECT state FROM current_strategy_candidate_classifications WHERE strategy_version_id=:id"), {"id": data["strategy_version_id"]}).scalar_one() == "OPERATIONAL_CANDIDATE"
        with engine.begin() as demoter:
            _demote(demoter, data)
        try:
            with pytest.raises(IntegrityError, match="REQUIRES_READ_COMMITTED"):
                with stale.begin_nested():
                    _complete(stale, data)
        finally:
            transaction.rollback()
    assert _counts(engine, data) == (0, 0, 0)


@pytest.mark.integration
@pytest.mark.parametrize("which", ["control", "variant"])
def test_committed_research_invalidation_blocks_new_claim(eligible, which):
    engine, data = eligible
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO experiment_invalidations(experiment_id,reason) VALUES (:id,'Synthetic evidence revocation')"), {"id": data[f"{which}_experiment_id"]})
    with engine.begin() as connection:
        with pytest.raises(IntegrityError, match="AUTHORIZATION_RESEARCH_INVALID"):
            with connection.begin_nested():
                _complete(connection, data)
    assert _counts(engine, data) == (0, 0, 0)


@pytest.mark.integration
def test_demotion_winner_waiter_uses_fresh_read_committed_evidence(eligible):
    engine, data = eligible
    ready, pid = Event(), []
    with engine.connect() as demoter:
        transaction = demoter.begin()
        _demote(demoter, data)
        def worker():
            with engine.begin() as connection:
                pid.append(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
                ready.set()
                with pytest.raises(IntegrityError, match="AUTHORIZATION_INELIGIBLE"):
                    with connection.begin_nested():
                        _complete(connection, data)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(worker)
                assert ready.wait(timeout=5)
                _wait_for_block(engine, pid[0])
                transaction.commit()
                future.result(timeout=20)
        finally:
            if transaction.is_active:
                transaction.rollback()
    assert _counts(engine, data) == (0, 0, 0)


@pytest.mark.integration
@pytest.mark.parametrize("revocation", ["demotion", "control", "variant"])
def test_authorization_winner_serializes_revocation_until_committed_effects(eligible, revocation):
    engine, data = eligible
    ready, pid = Event(), []
    with engine.connect() as owner:
        transaction = owner.begin()
        assert _complete(owner, data)
        def revoke():
            with engine.begin() as connection:
                pid.append(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
                ready.set()
                if revocation == "demotion":
                    _demote(connection, data)
                else:
                    connection.execute(text("INSERT INTO experiment_invalidations(experiment_id,reason) VALUES (:id,'Synthetic concurrent revocation')"), {"id": data[f"{revocation}_experiment_id"]})
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(revoke)
                assert ready.wait(timeout=5)
                _wait_for_block(engine, pid[0])
                transaction.commit()
                future.result(timeout=20)
        finally:
            if transaction.is_active:
                transaction.rollback()
    assert _counts(engine, data) == (1, 1, 1)
    with engine.begin() as connection:
        assert not _record(connection, data)  # immutable historical receipt survives
        assert SqlAlchemyJobRunRepository(connection).get_record_for_run(data["run"]).status is JobRunStatus.SUCCEEDED


@pytest.mark.integration
def test_simultaneous_authorized_claims_create_one_receipt_and_one_no_trade(eligible):
    engine, data = eligible
    barrier = Barrier(2)
    def worker():
        with engine.begin() as connection:
            barrier.wait(timeout=5)
            return _complete(connection, data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        assert sorted(future.result(timeout=30) for future in futures) == [False, True]
    assert _counts(engine, data) == (1, 1, 1)


@pytest.mark.integration
def test_connection_loss_before_commit_rolls_back_authorization_and_effects(eligible):
    engine, data = eligible
    connection = engine.connect()
    transaction = connection.begin()
    pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
    try:
        assert _complete(connection, data)
        with engine.begin() as killer:
            assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}).scalar_one()
        connection.invalidate()
    finally:
        transaction.rollback()
        connection.close()
    assert _counts(engine, data) == (0, 0, 0)
    with engine.begin() as connection:
        assert _complete(connection, data)
    assert _counts(engine, data) == (1, 1, 1)


@pytest.mark.integration
def test_completed_effect_recovery_after_demotion_preserves_receipt(eligible):
    engine, data = eligible
    with engine.begin() as connection:
        _assert(connection, data)
        assert SqlAlchemyJobRunRepository(connection).claim(data["run"])
        assert _record(connection, data)
        assert PaperNoTradeWriter(SqlAlchemyPaperNoTradeRepository(connection)).record(PaperCycleContext(data["run"]), strategy_version="v1", decision_time=data["as_of"], inputs_hash="a" * 64)
    with engine.begin() as connection:
        _demote(connection, data)
    with engine.begin() as connection:
        assert reconcile_completed_paper_run(connection, data["run"], current=data["as_of"] + timedelta(minutes=1))
        assert not _record(connection, data)
    assert _counts(engine, data) == (1, 1, 1)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"), {"id": str(data["run"].job_run_id)}).scalar_one() == 1


@pytest.mark.integration
def test_receipt_is_immutable_and_scope_provenance_cannot_be_forged(eligible):
    engine, data = eligible
    with engine.begin() as connection:
        assert _complete(connection, data)
        for sql in ("DELETE FROM paper_strategy_authorizations WHERE job_run_id=:id", "UPDATE paper_strategy_authorizations SET market='INDIA' WHERE job_run_id=:id"):
            with pytest.raises(IntegrityError, match="AUTHORIZATION_IMMUTABLE"):
                with connection.begin_nested():
                    connection.execute(text(sql), {"id": data["run"].job_run_id})
    assert _counts(engine, data) == (1, 1, 1)

@pytest.mark.integration
@pytest.mark.parametrize("changed", ["market", "classification", "decision", "job", "principal", "timestamp"])
def test_direct_sql_authorization_cannot_forge_scope_or_provenance(eligible, changed):
    engine, data = eligible
    with engine.begin() as connection:
        _assert(connection, data)
        assert SqlAlchemyJobRunRepository(connection).claim(data["run"])
        values = dict(job=data["run"].job_run_id, strategy=data["strategy_version_id"], market="USA",
            classification=data["classification_id"], decision=data["research_decision_id"])
        if changed == "market":
            values["market"] = "INDIA"
        elif changed == "classification":
            values["classification"] = uuid4()
        elif changed == "decision":
            values["decision"] = "M2-MISMATCHED-DECISION"
        elif changed == "job":
            values["job"] = uuid4()
        columns = "job_run_id,strategy_version_id,market,classification_id,research_decision_id"
        placeholders = ":job,:strategy,:market,:classification,:decision"
        if changed == "principal":
            columns += ",database_principal"
            placeholders += ",'forged-database-principal'"
        if changed == "timestamp":
            columns += ",created_at"
            placeholders += ",transaction_timestamp()+interval '1 second'"
        with pytest.raises(IntegrityError, match="PAPER_STRATEGY_AUTHORIZATION_"):
            with connection.begin_nested():
                connection.execute(text(f"INSERT INTO paper_strategy_authorizations({columns}) VALUES ({placeholders})"), values)
        assert SqlAlchemyPaperStrategyAuthorizationRepository(connection).get(data["run"].job_run_id) is None
        assert _record(connection, data)
        assert not _record(connection, data)
    assert _counts(engine, data) == (1, 1, 0)


@pytest.mark.integration
def test_repromotion_requires_the_new_exact_classification(eligible):
    engine, data = eligible
    with engine.begin() as connection:
        _demote(connection, data)
        definition = StrategyCandidateClassification(strategy_version_id=data["strategy_version_id"],
            markets=frozenset({StrategyMarket.USA}), state=StrategyCandidateState.OPERATIONAL_CANDIDATE,
            research_decision_id=data["research_decision_id"], rationale="Explicit synthetic reviewer re-promotion")
        repository = SqlAlchemyStrategyCandidateRepository(connection)
        assert repository.append(definition)
        new_classification = repository.deterministic_id(definition)
    with engine.begin() as connection:
        with pytest.raises(IntegrityError, match="AUTHORIZATION_INELIGIBLE"):
            with connection.begin_nested():
                _complete(connection, data)
        updated = {**data, "classification_id": new_classification}
        assert _complete(connection, updated)
    assert _counts(engine, data) == (1, 1, 1)
    with engine.connect() as connection:
        assert SqlAlchemyPaperStrategyAuthorizationRepository(connection).get(data["run"].job_run_id)["classification_id"] == new_classification
        with pytest.raises(RuntimeError, match="RECEIPT_CONFLICT"):
            _record(connection, data)


@pytest.mark.integration
def test_promotion_winner_and_new_authorization_linearize_at_commit(eligible):
    engine, data = eligible
    with engine.begin() as connection:
        _demote(connection, data)
    ready, pid = Event(), []
    with engine.connect() as promoter:
        transaction = promoter.begin()
        definition = StrategyCandidateClassification(strategy_version_id=data["strategy_version_id"],
            markets=frozenset({StrategyMarket.USA}), state=StrategyCandidateState.OPERATIONAL_CANDIDATE,
            research_decision_id=data["research_decision_id"], rationale="Concurrent synthetic re-promotion")
        repository = SqlAlchemyStrategyCandidateRepository(promoter)
        assert repository.append(definition)
        updated = {**data, "classification_id": repository.deterministic_id(definition)}
        def worker():
            with engine.begin() as connection:
                pid.append(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
                ready.set()
                return _complete(connection, updated)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(worker)
                assert ready.wait(timeout=5)
                _wait_for_block(engine, pid[0])
                transaction.commit()
                assert future.result(timeout=20)
        finally:
            if transaction.is_active:
                transaction.rollback()
    assert _counts(engine, data) == (1, 1, 1)
