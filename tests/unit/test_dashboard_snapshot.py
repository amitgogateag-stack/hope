"""Dashboard snapshot is read-only, aggregate-only and fail-closed."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hope.api.dashboard_snapshot import build_dashboard_snapshot, export_dashboard_snapshot


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self.scalar = scalar
        self.rows = rows

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar

    def all(self):
        return self.rows


class _Connection:
    def __init__(self, state="RUNNING", counts=None, recovery=(1, 0, 1)):
        self.statements = []
        self.state = state
        self.counts = counts if counts is not None else [("CLAIMED", 1), ("SUCCEEDED", 2)]
        self.recovery = recovery

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if sql.startswith("SET TRANSACTION"):
            return _Result()
        if sql.startswith("SELECT CURRENT_TIMESTAMP"):
            return _Result(scalar=datetime(2026, 10, 12, tzinfo=timezone.utc))
        if "FROM paper_environment_control_events" in sql:
            return _Result(scalar=self.state)
        if "FROM research_runs" in sql:
            return _Result(scalar=3)
        if "FROM paper_effects" in sql:
            return _Result(scalar=self.recovery[0])
        if "INTERVAL '1 hour'" in sql:
            return _Result(scalar=self.recovery[1])
        if "FROM audit_events" in sql:
            return _Result(scalar=self.recovery[2])
        if "FROM job_runs" in sql:
            return _Result(rows=self.counts)
        raise AssertionError(f"Unexpected dashboard SQL: {sql}")


def test_dashboard_snapshot_counts_and_transaction_is_read_only():
    connection = _Connection()
    snapshot = build_dashboard_snapshot(connection)
    assert connection.statements[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    assert snapshot["paper"]["environment_state"] == "RUNNING"
    assert snapshot["paper"]["job_runs"] == {
        "total": 3, "claimed": 1, "succeeded": 2, "failed": 0,
    }
    assert snapshot["research"]["run_count"] == 3
    assert snapshot["generated_at"] == "2026-10-12T00:00:00+00:00"
    assert snapshot["paper"]["recovery"] == {
        "claimed_with_effects": 1, "claimed_without_effects": 0,
        "claims_older_than_one_hour": 0, "reconciliation_receipts": 1,
    }
    assert not any(
        stmt.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE"))
        for stmt in connection.statements
    )


@pytest.mark.parametrize("state", [None, "UNKNOWN", ""])
def test_dashboard_refuses_missing_or_invalid_paper_control_state(state):
    with pytest.raises(RuntimeError, match="HOPE_DASHBOARD_PAPER_STATE_UNVERIFIED"):
        build_dashboard_snapshot(_Connection(state=state))


@pytest.mark.parametrize("counts", [
    [("CLAIMED", -1)], [("RUNNING", 1)], [("FAILED", 1.5)],
])
def test_dashboard_refuses_corrupt_job_status_or_count(counts):
    with pytest.raises((ValueError, RuntimeError)):
        build_dashboard_snapshot(_Connection(counts=counts))


def test_export_rejects_missing_database_url_without_creating_file(tmp_path: Path):
    target = tmp_path / "dashboard.json"
    with pytest.raises(ValueError, match="HOPE_DATABASE_URL_REQUIRED"):
        export_dashboard_snapshot(target, "")
    assert not target.exists()


@pytest.mark.parametrize("recovery", [
    (2, 0, 0), (0, 2, 0), (0, 0, 3), (-1, 0, 0),
    (True, 0, 0), (0, 0.5, 0),
])
def test_dashboard_rejects_invalid_or_inconsistent_recovery_counts(recovery):
    with pytest.raises(ValueError):
        build_dashboard_snapshot(_Connection(recovery=recovery))


def test_dashboard_empty_database_has_zero_backlog_without_inventing_claims():
    snapshot = build_dashboard_snapshot(_Connection(counts=[], recovery=(0, 0, 0)))
    assert set(snapshot["paper"]["recovery"].values()) == {0}


def test_failed_recovery_read_preserves_last_complete_export(tmp_path, monkeypatch):
    from hope.api import dashboard_snapshot as module

    class BrokenConnection(_Connection):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement):
            if "FROM paper_effects" in str(statement):
                raise RuntimeError("simulated interrupted recovery read")
            return super().execute(statement)

    class Engine:
        disposed = False

        def connect(self):
            return BrokenConnection()

        def dispose(self):
            self.disposed = True

    engine = Engine()
    monkeypatch.setattr(module, "create_engine", lambda _: engine)
    target = tmp_path / "snapshot.json"
    target.write_bytes(b"last complete private snapshot")
    with pytest.raises(RuntimeError, match="interrupted recovery read"):
        export_dashboard_snapshot(target, "configured-private-database")
    assert target.read_bytes() == b"last complete private snapshot"
    assert engine.disposed
    assert list(tmp_path.iterdir()) == [target]
