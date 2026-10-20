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
from sqlalchemy.exc import DBAPIError

from hope.api.dashboard_projection import EXPERIMENT_STATES, VERDICTS, verified_projections
from hope.api.dashboard_m2_snapshot import readers as m2_readers
from hope.infrastructure.durable_file import durable_replace


def _count(connection: Connection, query: str) -> int:
    result = connection.execute(text(query)).scalar_one()
    if type(result) is not int or result < 0:
        raise ValueError("HOPE_DASHBOARD_INVALID_COUNT")
    return result


def _validate_read_budget(statement_timeout_ms: int, lock_timeout_ms: int) -> None:
    if (type(statement_timeout_ms) is not int or not 1 <= statement_timeout_ms <= 120000
            or type(lock_timeout_ms) is not int or not 1 <= lock_timeout_ms <= statement_timeout_ms):
        raise ValueError("HOPE_DASHBOARD_READ_TIMEOUT_INVALID")


def _optional_projections(connection: Connection, generated: datetime) -> dict:
    def scalar(query):
        return connection.execute(text(query)).scalar_one()
    def grouped(table, column, fields):
        # Field vocabularies are constants; unknown experiment labels collapse
        # into OTHER rather than exposing arbitrary text stored in the database.
        expression = column
        if table == "experiments":
            allowed = ",".join(f"'{key}'" for key in fields if key != "OTHER")
            expression = f"CASE WHEN status IN ({allowed}) THEN status ELSE 'OTHER' END"
        rows = dict(connection.execute(text(f"SELECT {expression}, count(*) FROM {table} GROUP BY 1")).all())
        if any(key not in fields for key in rows):
            raise ValueError("HOPE_DASHBOARD_PROJECTION_UNKNOWN_VERDICT")
        return {key: rows.get(key, 0) for key in fields}
    def research_experiments():
        counts = grouped("experiments", "status", EXPERIMENT_STATES)
        return {"total": sum(counts.values()), "by_status": counts}
    def decisions():
        counts = grouped("research_decisions", "decision", VERDICTS)
        return {"total": sum(counts.values()), "by_verdict": counts}
    def paper_activity():
        last = scalar("SELECT max(completed_at) FROM job_runs WHERE left(job_key,6)='paper:' AND status='SUCCEEDED'")
        no_trade = scalar("SELECT count(*) FROM paper_no_trade_decisions n JOIN job_runs j ON j.job_run_id=n.job_run_id "
                          "WHERE left(j.job_key,6)='paper:' AND j.status='SUCCEEDED'")
        return {"last_completed_at": last.isoformat() if last is not None else None, "succeeded_no_trade": no_trade}
    def market_data():
        total, event, available = connection.execute(text(
            "SELECT count(*), max(event_time), max(available_time) FROM market_bars"
        )).one()
        if any(value is not None and isinstance(value, datetime) and value.tzinfo is not None and value > generated
               for value in (event, available)):
            # Future-known source records cannot establish current market data
            # freshness. Keep other authenticated sections, infer no zero bars.
            return {"status": "UNAVAILABLE"}
        return {"bar_count": total, "latest_event_at": event.isoformat() if event else None,
                "latest_available_at": available.isoformat() if available else None, "freshness_policy": "UNKNOWN"}
    readers = {
        "datasets": lambda: {"registered": scalar("SELECT count(*) FROM datasets"),
                              "pit_certified": scalar("SELECT count(*) FROM datasets WHERE pit_certified IS TRUE")},
        "strategies": lambda: {"versions": scalar("SELECT count(*) FROM strategy_versions")},
        "experiments": research_experiments, "decisions": decisions,
        "paper_activity": paper_activity, "market_data": market_data,
    }
    readers.update(m2_readers(connection, generated))
    result = {"portfolio": {"status": "UNAVAILABLE"}}
    for name, reader in readers.items():
        try:
            with connection.begin_nested():
                result[name] = {"status": "AVAILABLE", **reader()}
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) not in {"42501", "42P01", "42703"}:
                raise
            # Optional schema/privilege gaps never manufacture zero activity and
            # never poison the read-only transaction's other verified sections.
            result[name] = {"status": "UNAVAILABLE"}
    return verified_projections(result, generated)


def build_dashboard_snapshot(
    connection: Connection, *, statement_timeout_ms: int = 15000, lock_timeout_ms: int = 3000,
) -> dict[str, object]:
    """Read a transactionally consistent snapshot; fail closed on missing state."""
    # The first SQL statement establishes a read-only repeatable-read snapshot.
    # No lock, broker credential or mutation is required by the dashboard.
    _validate_read_budget(statement_timeout_ms, lock_timeout_ms)
    connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    # Transaction-local limits protect the exporter from DDL locks and expensive
    # reads without modifying the authenticated role or leaking settings into a
    # pooled connection. Values are validated integers, never SQL input strings.
    connection.execute(text(f"SET LOCAL statement_timeout = {statement_timeout_ms}"))
    connection.execute(text(f"SET LOCAL lock_timeout = {lock_timeout_ms}"))
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
    # EXISTS avoids multiplying claims by ledger cardinality. These counts are
    # observations only: effects do not prove that a run is safe to reconcile.
    claims_with_effects = _count(connection,
        "SELECT count(*) FROM job_runs j WHERE left(j.job_key, 6) = 'paper:' "
        "AND j.status = 'CLAIMED' AND EXISTS "
        "(SELECT 1 FROM paper_effects e WHERE e.job_run_id = j.job_run_id)")
    stale_claims = _count(connection,
        "SELECT count(*) FROM job_runs WHERE left(job_key, 6) = 'paper:' "
        "AND status = 'CLAIMED' AND created_at < CURRENT_TIMESTAMP - INTERVAL '1 hour'")
    receipts = _count(connection,
        "SELECT count(*) FROM audit_events a JOIN job_runs j "
        "ON a.entity_id = CAST(j.job_run_id AS text) "
        "WHERE a.event_type = 'PAPER_RUN_RECONCILED' AND a.entity_type = 'JOB_RUN' "
        "AND left(j.job_key, 6) = 'paper:' AND j.status = 'SUCCEEDED'")
    claimed = jobs.get("CLAIMED", 0)
    if claims_with_effects > claimed or stale_claims > claimed or receipts > jobs.get("SUCCEEDED", 0):
        raise ValueError("HOPE_DASHBOARD_RECOVERY_COUNTS_INCONSISTENT")
    return {
        "schema_version": 1,
        "source": "hope_postgresql",
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(),
        "research": {"run_count": research_runs},
        "projections": _optional_projections(connection, timestamp),
        "paper": {
            "environment_state": state,
            "recovery": {
                "claimed_with_effects": claims_with_effects,
                "claimed_without_effects": claimed - claims_with_effects,
                "claims_older_than_one_hour": stale_claims,
                "reconciliation_receipts": receipts,
            },
            "job_runs": {
                "total": sum(jobs.values()),
                "claimed": jobs.get("CLAIMED", 0),
                "succeeded": jobs.get("SUCCEEDED", 0),
                "failed": jobs.get("FAILED", 0),
            },
        },
    }


def export_dashboard_snapshot(
    output: Path, database_url: str, *, connect_timeout_seconds: int = 10,
    statement_timeout_ms: int = 15000, lock_timeout_ms: int = 3000,
) -> Path:
    """Atomically publish only a complete snapshot; never publish partial reads."""
    if not database_url:
        raise ValueError("HOPE_DATABASE_URL_REQUIRED")
    _validate_read_budget(statement_timeout_ms, lock_timeout_ms)
    if type(connect_timeout_seconds) is not int or not 1 <= connect_timeout_seconds <= 60:
        raise ValueError("HOPE_DASHBOARD_CONNECT_TIMEOUT_INVALID")
    engine = create_engine(database_url, connect_args={"connect_timeout": connect_timeout_seconds})
    try:
        with engine.connect() as connection:
            snapshot = build_dashboard_snapshot(
                connection, statement_timeout_ms=statement_timeout_ms, lock_timeout_ms=lock_timeout_ms,
            )
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
        durable_replace(temp_name, output)
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
