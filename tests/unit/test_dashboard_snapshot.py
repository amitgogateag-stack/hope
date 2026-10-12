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
    def __init__(self, state="RUNNING", counts=None):
        self.statements = []
        self.state = state
        self.counts = counts if counts is not None else [("CLAIMED", 1), ("SUCCEEDED", 2)]

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
