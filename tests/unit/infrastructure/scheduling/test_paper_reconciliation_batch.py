from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import repeat
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from hope.application.jobs import create_scheduled_job_run
from hope.infrastructure.scheduling import paper_reconciliation_batch as batch


NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


def _run(key=None):
    return create_scheduled_job_run(key or f"paper:USA:{uuid4()}:batch", NOW)


@pytest.fixture
def harness(monkeypatch):
    events = []

    @contextmanager
    def savepoint():
        events.append("begin")
        try:
            yield
        except BaseException:
            events.append("rollback")
            raise
        else:
            events.append("release")

    connection = SimpleNamespace(begin_nested=savepoint)
    monkeypatch.setattr(batch, "SqlAlchemyPaperEnvironmentControlRepository", lambda c:
                        SimpleNamespace(assert_running=lambda: events.append("control")))
    monkeypatch.setattr(batch, "SqlAlchemyJobRunRepository", lambda c:
                        SimpleNamespace(lock_claimed_for_reconciliation=lambda r:
                                        events.append(("lock", r.job_run_id))))

    def reconcile(c, run, **kwargs):
        events.append(("proof", run.job_run_id))
        return True

    monkeypatch.setattr(batch, "_reconcile_completed_paper_run", reconcile)
    return connection, events


@pytest.mark.parametrize("limit", [0, -1, 33, True, 1.0, None])
def test_invalid_limit_does_not_touch_database(harness, limit):
    connection, events = harness
    with pytest.raises(ValueError, match="LIMIT_INVALID"):
        batch.reconcile_completed_paper_batch(connection, [_run()], current=NOW, max_runs=limit)
    assert events == []


def test_infinite_input_consumption_is_bounded(harness):
    connection, events = harness
    consumed = []

    def source():
        for run in repeat(_run()):
            consumed.append(run)
            yield run

    with pytest.raises(ValueError, match="LIMIT_EXCEEDED"):
        batch.reconcile_completed_paper_batch(connection, source(), current=NOW, max_runs=2)
    assert len(consumed) == 3
    assert events == []


@pytest.mark.parametrize("kind", ["empty", "duplicate", "live", "type", "clock"])
def test_invalid_selection_has_no_database_side_effect(harness, kind):
    connection, events = harness
    run = _run()
    runs = {"empty": [], "duplicate": [run, run], "live": [_run("live:USA:test")],
            "type": [object()], "clock": [run]}[kind]
    with pytest.raises((ValueError, TypeError, RuntimeError)):
        batch.reconcile_completed_paper_batch(
            connection, runs, current=NOW.replace(tzinfo=None) if kind == "clock" else NOW,
        )
    assert events == []


def test_all_locks_precede_proof_in_canonical_order(harness):
    connection, events = harness
    runs = sorted([_run(), _run(), _run()], key=lambda r: r.job_run_id.int)
    result = batch.reconcile_completed_paper_batch(connection, reversed(runs), current=NOW)
    ids = tuple(r.job_run_id for r in runs)
    assert result.reconciled_run_ids == ids
    assert result.already_reconciled_run_ids == ()
    assert events == ["begin", "control", *[("lock", i) for i in ids],
                      *[("proof", i) for i in ids], "release"]


def test_existing_receipts_are_verified_and_reported(harness, monkeypatch):
    connection, events = harness
    runs = [_run(), _run()]
    seen = []

    def reconcile(c, run, **kwargs):
        seen.append(run)
        return run == runs[0]

    monkeypatch.setattr(batch, "_reconcile_completed_paper_run", reconcile)
    result = batch.reconcile_completed_paper_batch(connection, runs, current=NOW)
    assert set(seen) == set(runs)
    assert result.reconciled_run_ids == (runs[0].job_run_id,)
    assert result.already_reconciled_run_ids == (runs[1].job_run_id,)


def test_claimed_proofs_precede_terminal_history_reverification_after_all_locks(harness, monkeypatch):
    connection, events = harness
    runs = sorted([_run(), _run(), _run()], key=lambda r: r.job_run_id.int)

    def lock(run):
        events.append(("lock", run.job_run_id))
        return run != runs[0]

    monkeypatch.setattr(batch, "SqlAlchemyJobRunRepository", lambda c:
                        SimpleNamespace(lock_claimed_for_reconciliation=lock))
    batch.reconcile_completed_paper_batch(connection, reversed(runs), current=NOW)
    ids = tuple(r.job_run_id for r in runs)
    assert events == ["begin", "control", *[("lock", i) for i in ids],
                      ("proof", ids[1]), ("proof", ids[2]), ("proof", ids[0]), "release"]


@pytest.mark.parametrize("failure", [RuntimeError("proof failed"), SQLAlchemyError("db failed"), KeyboardInterrupt()])
def test_failure_rolls_back_whole_batch(harness, monkeypatch, failure):
    connection, events = harness
    calls = []

    def reconcile(c, run, **kwargs):
        calls.append(run)
        if len(calls) == 2:
            raise failure
        return True

    monkeypatch.setattr(batch, "_reconcile_completed_paper_run", reconcile)
    with pytest.raises(type(failure) if not isinstance(failure, SQLAlchemyError) else RuntimeError):
        batch.reconcile_completed_paper_batch(connection, [_run(), _run()], current=NOW)
    assert len(calls) == 2
    assert events[-1] == "rollback"
