"""Read-only, aggregate-only snapshot for a private HOPE dashboard.

This module does not start PAPER jobs, reconcile effects, or expose broker actions.
The export is local: operators decide where the resulting JSON is served.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Connection, create_engine, text


def _count(connection: Connection, query: str) -> int:
    result = connection.execute(text(query)).scalar_one()
    if type(result) is not int or result < 0:
        raise ValueError("HOPE_DASHBOARD_INVALID_COUNT")
    return result


def build_dashboard_snapshot(connection: Connection) -> dict[str, object]:
    """Read a transactionally consistent snapshot; fail closed on missing state."""
    # The first SQL statement establishes a read-only repeatable-read snapshot.
    # No lock, broker credential or mutation is required by the dashboard.
    connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    timestamp = connection.execute(text("SELECT CURRENT_TIMESTAMP")).scalar_one()
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ValueError("HOPE_DASHBOARD_DATABASE_TIME_INVALID")
    state = connection.execute(
        text("SELECT state FROM paper_environment_control_events "
             "ORDER BY control_sequence DESC LIMIT 1")
    ).scalar_one_or_none()
    if state not in {"RUNNING", "HALTED"}:
        raise RuntimeError("HOPE_DASHBOARD_PAPER_STATE_UNVERIFIED")
    research_runs = _count(connection, "SELECT count(*) FROM research_runs")
    jobs = dict(connection.execute(
        text("SELECT status, count(*) AS n FROM job_runs "
             "WHERE left(job_key, 6) = 'paper:' GROUP BY status")
    ).all())
    if any(k not in {"CLAIMED", "SUCCEEDED", "FAILED"} for k in jobs):
        raise RuntimeError("HOPE_DASHBOARD_UNKNOWN_JOB_STATUS")
    if any(type(v) is not int or v < 0 for v in jobs.values()):
        raise ValueError("HOPE_DASHBOARD_INVALID_COUNT")
    return {
        "schema_version": 1,
        "source": "hope_postgresql",
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(),
        "research": {"run_count": research_runs},
        "paper": {
            "environment_state": state,
            "job_runs": {
                "total": sum(jobs.values()),
                "claimed": jobs.get("CLAIMED", 0),
                "succeeded": jobs.get("SUCCEEDED", 0),
                "failed": jobs.get("FAILED", 0),
            },
        },
    }


def export_dashboard_snapshot(output: Path, database_url: str) -> Path:
    """Atomically publish only a complete snapshot; never publish partial reads."""
    if not database_url:
        raise ValueError("HOPE_DATABASE_URL_REQUIRED")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            snapshot = build_dashboard_snapshot(connection)
            connection.rollback()
    finally:
        engine.dispose()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".hope-dashboard-", suffix=".json", dir=output.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, output)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export local read-only HOPE dashboard metrics")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export_dashboard_snapshot(args.output, os.environ.get("HOPE_DATABASE_URL", ""))
