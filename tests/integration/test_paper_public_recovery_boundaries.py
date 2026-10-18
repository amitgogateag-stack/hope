"""Public single-run recovery must remain atomic when a caller catches errors."""
import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_paper_reconciliation_batch_postgres import durable_batch, NOW
from hope.application.jobs import JobRunStatus, create_job_run_completion
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_reconciliation_audit import SqlAlchemyPaperReconciliationAuditRepository
from hope.infrastructure.scheduling import paper_reconciliation as recovery
from hope.infrastructure.scheduling import paper_recovery_limits as limits


def receipts(connection, run):
    return connection.execute(text("SELECT count(*) FROM audit_events WHERE "
        "event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),
        {"id":str(run.job_run_id)}).scalar_one()


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["false", "proof", "interrupt", "cancel"])
@pytest.mark.parametrize("existing", [False, True])
def test_authentication_final_verification_failure_does_not_commit_receipt(durable_batch, monkeypatch, failure, existing):
    engine, runs = durable_batch
    run = runs[0]
    with engine.begin() as connection:
        assert SqlAlchemyJobRunRepository(connection).complete(
            create_job_run_completion(run, JobRunStatus.SUCCEEDED, NOW))
        if existing:
            recovery.authenticate_successful_paper_run(connection, run)
    original = SqlAlchemyPaperReconciliationAuditRepository.verify
    calls = []
    def fail_final(self, *args):
        calls.append(True)
        # The repository's own insertion/reread succeeds; the public operation's
        # independent final verification then fails after its nested savepoint.
        if len(calls) == 1:
            return original(self, *args)
        if failure == "proof":
            raise ValueError("injected late proof error")
        if failure == "interrupt":
            raise KeyboardInterrupt()
        if failure == "cancel":
            self._connection.execute(text("SELECT pg_sleep(0.4)"))
        return False
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='90ms'"))
        connection.execute(text("CREATE TEMP TABLE public_recovery_marker(value int) ON COMMIT DROP"))
        connection.execute(text("INSERT INTO public_recovery_marker VALUES (1)"))
        with monkeypatch.context() as patch:
            patch.setattr(SqlAlchemyPaperReconciliationAuditRepository, "verify", fail_final)
            error = {"false":RuntimeError,"proof":ValueError,"interrupt":KeyboardInterrupt,"cancel":DBAPIError}[failure]
            with pytest.raises(error):
                recovery.authenticate_successful_paper_run(connection, run)
        assert len(calls) == 2
        assert connection.execute(text("SELECT value FROM public_recovery_marker")).scalar_one() == 1
        assert connection.execute(text("SHOW statement_timeout")).scalar_one() == "90ms"
        # Caller deliberately commits despite the caught proof failure.
    with engine.begin() as connection:
        assert receipts(connection, run) == int(existing)
        recovery.authenticate_successful_paper_run(connection, run)
        recovery.authenticate_successful_paper_run(connection, run)
        assert receipts(connection, run) == 1
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),
            {"id":run.job_run_id}).scalar_one() == 1


def prepare(engine, run, entry):
    if entry == "authenticate":
        with engine.begin() as connection:
            assert SqlAlchemyJobRunRepository(connection).complete(
                create_job_run_completion(run, JobRunStatus.SUCCEEDED, NOW))


def invoke(connection, run, entry):
    if entry == "authenticate":
        return recovery.authenticate_successful_paper_run(connection, run)
    return recovery.reconcile_completed_paper_run(connection, run, current=NOW)


@pytest.mark.integration
@pytest.mark.parametrize("entry", ["authenticate", "reconcile"])
@pytest.mark.parametrize("block", ["control", "job"])
def test_public_entrypoint_lock_timeout_preserves_outer_transaction(durable_batch, monkeypatch, entry, block):
    engine, runs = durable_batch
    run = runs[0]
    prepare(engine, run, entry)
    monkeypatch.setattr(limits, "RECOVERY_LOCK_TIMEOUT_MS", 80)
    with engine.begin() as blocker:
        if block == "control":
            blocker.execute(text("SELECT pg_advisory_xact_lock(hashtext('hope:paper:environment-control')::bigint)"))
        else:
            blocker.execute(text("SELECT job_run_id FROM job_runs WHERE job_run_id=:id FOR UPDATE"),
                {"id":run.job_run_id})
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout='3s'"))
            with pytest.raises(DBAPIError if entry == "authenticate" else RuntimeError) as caught:
                invoke(connection, run, entry)
            database_error = caught.value if entry == "authenticate" else caught.value.__cause__
            assert database_error.orig.sqlstate == "55P03"
            assert receipts(connection, run) == 0
            assert connection.execute(text("SHOW lock_timeout")).scalar_one() == "0"
            assert connection.execute(text("SHOW statement_timeout")).scalar_one() == "3s"
    with engine.begin() as connection:
        invoke(connection, run, entry)
        assert receipts(connection, run) == 1


@pytest.mark.integration
@pytest.mark.parametrize("entry", ["authenticate", "reconcile"])
def test_backend_death_after_return_does_not_commit_authentication(durable_batch, entry):
    engine, runs = durable_batch
    run = runs[0]
    prepare(engine, run, entry)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        backend = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        invoke(connection, run, entry)
        assert receipts(connection, run) == 1
        with engine.begin() as killer:
            assert killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid":backend}).scalar_one()
        connection.invalidate()
    finally:
        transaction.rollback()
        connection.close()
    with engine.begin() as restarted:
        assert receipts(restarted, run) == 0
        assert SqlAlchemyJobRunRepository(restarted).get_record_for_run(run).status is (
            JobRunStatus.SUCCEEDED if entry == "authenticate" else JobRunStatus.CLAIMED)
        invoke(restarted, run, entry)
        invoke(restarted, run, entry)
        assert receipts(restarted, run) == 1


@pytest.mark.integration
def test_concurrent_authentication_commits_one_receipt(durable_batch):
    engine, runs = durable_batch
    run = runs[0]
    prepare(engine, run, "authenticate")
    barrier = Barrier(2)
    def authenticate():
        with engine.begin() as connection:
            barrier.wait(timeout=10)
            recovery.authenticate_successful_paper_run(connection, run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(authenticate) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    with engine.connect() as connection:
        assert receipts(connection, run) == 1
