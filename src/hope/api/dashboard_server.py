"""Loopback-only read-only HOPE dashboard HTTP server.

Use an authenticated SSH tunnel to reach it; never expose this port publicly.
"""
from __future__ import annotations

import argparse
import json
from re import fullmatch
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hope.api.dashboard_projection import verified_projections

OPERATIONS_ERROR_CODES = {
        None, "PAPER_ENVIRONMENT_HALTED", "PAPER_SERVICE_DATABASE_UNAVAILABLE",
        "PAPER_SERVICE_SCHEMA_FILES_UNAVAILABLE", "PAPER_SERVICE_SCHEMA_INCOMPATIBLE",
        "PAPER_SERVICE_PLAN_UNAVAILABLE", "PAPER_SERVICE_RUN_LIMIT_EXCEEDED",
        "PAPER_SERVICE_CLOCK_INVALID", "PAPER_SCHEDULER_CONCURRENT_RUN",
        "PAPER_JOB_INCOMPLETE_PRIOR_CLAIM", "PAPER_MARKET_DATA_STALE",
        "PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT", "PAPER_SERVICE_CYCLE_FAILED",
        "PAPER_SERVICE_CERTIFIED_BINDING_REQUIRED",
        "PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE", "PAPER_STRATEGY_AUTHORIZATION_RESEARCH_INVALID",
        "PAPER_CERTIFIED_INPUT_UNAVAILABLE",
}


def _verified_snapshot(path: Path) -> tuple[bytes, datetime]:
    """Reject malformed, partial, future-dated or oversized snapshot files."""
    if path.stat().st_size > 32768:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_TOO_LARGE")
    payload = path.read_bytes()
    if len(payload) > 32768:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_TOO_LARGE")
    data = json.loads(payload)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1 or data.get("source") != "hope_postgresql":
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INVALID")
    commit = data.get("deployed_commit")
    if commit is not None and (not isinstance(commit, str) or fullmatch(r"[0-9a-f]{40}", commit) is None):
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_COMMIT_INVALID")
    research = data.get("research")
    paper = data.get("paper")
    if not isinstance(research, dict) or not isinstance(paper, dict):
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INCOMPLETE")
    counts = paper.get("job_runs")
    valid_count = lambda n: type(n) is int and 0 <= n <= 9007199254740991
    if not valid_count(research.get("run_count")) or paper.get("environment_state") not in {"RUNNING", "HALTED"}:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INCOMPLETE")
    if not isinstance(counts, dict) or not all(
        valid_count(counts.get(key)) for key in ("total", "claimed", "succeeded", "failed")
    ):
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INCOMPLETE")
    if counts["total"] != counts["claimed"] + counts["succeeded"] + counts["failed"]:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INCONSISTENT")
    recovery = paper.get("recovery")
    recovery_fields = (
        "claimed_with_effects", "claimed_without_effects",
        "claims_older_than_one_hour", "reconciliation_receipts",
    )
    if "recovery" in paper:
        if not isinstance(recovery, dict) or not all(valid_count(recovery.get(k)) for k in recovery_fields):
            raise ValueError("HOPE_DASHBOARD_RECOVERY_COUNTS_INVALID")
        if (recovery["claimed_with_effects"] + recovery["claimed_without_effects"] != counts["claimed"]
                or recovery["claims_older_than_one_hour"] > counts["claimed"]
                or recovery["reconciliation_receipts"] > counts["succeeded"]):
            raise ValueError("HOPE_DASHBOARD_RECOVERY_COUNTS_INCONSISTENT")
    stamp = data.get("generated_at")
    if not isinstance(stamp, str):
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_TIMESTAMP_INVALID")
    try:
        generated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_TIMESTAMP_INVALID") from exc
    if generated.tzinfo is None:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_TIMESTAMP_INVALID")
    if (generated - datetime.now(timezone.utc)).total_seconds() > 300:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_FROM_FUTURE")
    # The local snapshot file is an untrusted input boundary. Never serve
    # arbitrary top-level or nested fields that could contain identifiers,
    # credentials, fills, positions, or other sensitive database material.
    safe = {
        "schema_version": 1,
        "source": "hope_postgresql",
        "generated_at": generated.isoformat(),
        "research": {"run_count": research["run_count"]},
        "paper": {
            "environment_state": paper["environment_state"],
            "job_runs": {
                key: counts[key]
                for key in ("total", "claimed", "succeeded", "failed")
            },
        },
    }
    if commit is not None:
        safe["deployed_commit"] = commit
    if recovery is not None:
        safe["paper"]["recovery"] = {key: recovery[key] for key in recovery_fields}
    if "projections" in data:
        safe["projections"] = verified_projections(data["projections"], generated)
    return json.dumps(safe, separators=(",", ":")).encode("utf-8"), generated


def _verified_legacy_inventory(path: Path) -> tuple[bytes, datetime]:
    """Return only a sanitized legacy registration inventory, never PAPER data."""
    if path.stat().st_size > 8192:
        raise ValueError("HOPE_LEGACY_INVENTORY_TOO_LARGE")
    raw = path.read_bytes()
    if len(raw) > 8192:
        raise ValueError("HOPE_LEGACY_INVENTORY_TOO_LARGE")
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("HOPE_LEGACY_INVENTORY_INVALID")
    if data.get("source") != "hope_legacy_research_inventory" or data.get("schema_compatible") is not True:
        raise ValueError("HOPE_LEGACY_INVENTORY_INCOMPATIBLE")
    if data.get("paper") is not None or data.get("missing_columns") != {}:
        raise ValueError("HOPE_LEGACY_INVENTORY_INCONSISTENT")
    counts = data.get("research")
    fields = ("registered_experiments", "registered_strategy_versions", "registered_datasets")
    if not isinstance(counts, dict) or set(counts) != set(fields):
        raise ValueError("HOPE_LEGACY_INVENTORY_INVALID_COUNTS")
    if not all(type(counts[k]) is int and 0 <= counts[k] <= 9007199254740991 for k in fields):
        raise ValueError("HOPE_LEGACY_INVENTORY_INVALID_COUNTS")
    stamp = data.get("generated_at")
    if not isinstance(stamp, str):
        raise ValueError("HOPE_LEGACY_INVENTORY_TIMESTAMP_INVALID")
    try:
        generated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("HOPE_LEGACY_INVENTORY_TIMESTAMP_INVALID") from exc
    if generated.tzinfo is None or (generated - datetime.now(timezone.utc)).total_seconds() > 300:
        raise ValueError("HOPE_LEGACY_INVENTORY_TIMESTAMP_INVALID")
    # Whitelist exact aggregate fields so an unexpected file cannot leak metadata.
    safe = {
        "schema_version": 1, "source": "hope_legacy_research_inventory",
        "generated_at": generated.isoformat(), "schema_compatible": True,
        "research": {k: counts[k] for k in fields}, "paper": None,
    }
    return json.dumps(safe, separators=(",", ":")).encode("utf-8"), generated


def _verified_operations(path: Path) -> bytes:
    """Sanitize an optional local service heartbeat, never expose exception text."""
    if path.stat().st_size > 8192:
        raise ValueError("HOPE_OPERATIONS_STATUS_TOO_LARGE")
    raw = path.read_bytes()
    if len(raw) > 8192:
        raise ValueError("HOPE_OPERATIONS_STATUS_TOO_LARGE")
    data = json.loads(raw)
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1 or data.get("paper_only") is not True
            or data.get("live_enabled") is not False
            or data.get("state") not in {"STARTING", "CURRENT", "HALTED", "UNAVAILABLE", "STOPPED"}):
        raise ValueError("HOPE_OPERATIONS_STATUS_INVALID")
    fields = ("cycles_succeeded", "cycles_failed", "jobs_executed", "jobs_skipped")
    if not all(type(data.get(k)) is int and 0 <= data[k] <= 9007199254740991 for k in fields):
        raise ValueError("HOPE_OPERATIONS_STATUS_COUNTS_INVALID")
    def stamp(value):
        if not isinstance(value, str):
            raise ValueError("HOPE_OPERATIONS_STATUS_TIME_INVALID")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or (parsed - datetime.now(timezone.utc)).total_seconds() > 300:
            raise ValueError("HOPE_OPERATIONS_STATUS_TIME_INVALID")
        return parsed
    generated = stamp(data.get("generated_at"))
    success = None if data.get("last_success_at") is None else stamp(data["last_success_at"])
    if success is not None and success > generated:
        raise ValueError("HOPE_OPERATIONS_STATUS_TIME_INVALID")
    if data["state"] == "CURRENT" and success is None:
        raise ValueError("HOPE_OPERATIONS_STATUS_TIME_INVALID")
    commit = data.get("deployed_commit")
    compatibility = data.get("schema_compatibility", "UNKNOWN")
    if (commit is not None and (not isinstance(commit, str) or fullmatch(r"[0-9a-f]{40}", commit) is None)):
        raise ValueError("HOPE_OPERATIONS_COMMIT_INVALID")
    if compatibility not in {"UNKNOWN", "COMPATIBLE", "INCOMPATIBLE"}:
        raise ValueError("HOPE_OPERATIONS_SCHEMA_INVALID")
    # A closed vocabulary prevents a valid-looking file from leaking arbitrary
    # identifiers through a nominally sanitized error-code field.
    error = data.get("error_code")
    if error not in OPERATIONS_ERROR_CODES:
        error = "PAPER_SERVICE_CYCLE_FAILED"
    safe = {
        "schema_version": 1, "generated_at": generated.isoformat(),
        "state": data["state"], "last_success_at": success.isoformat() if success else None,
        "paper_only": True, "live_enabled": False, "error_code": error,
        "deployed_commit": commit, "schema_compatibility": compatibility,
        **{key: data[key] for key in fields},
    }
    if "last_failure_at" in data or "last_failure_code" in data:
        failed = None if data.get("last_failure_at") is None else stamp(data["last_failure_at"])
        code = data.get("last_failure_code")
        if failed is not None and failed > generated:
            raise ValueError("HOPE_OPERATIONS_STATUS_TIME_INVALID")
        # Reuse the same closed error vocabulary without exposing free text.
        if code is not None:
            code = code if isinstance(code,str) and code in OPERATIONS_ERROR_CODES else "PAPER_SERVICE_CYCLE_FAILED"
        if (failed is None)!=(code is None):
            raise ValueError("HOPE_OPERATIONS_STATUS_FAILURE_INVALID")
        safe.update(last_failure_at=failed.isoformat() if failed else None,last_failure_code=code)
    return json.dumps(safe, separators=(",", ":")).encode("utf-8")


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, preview: Path, snapshot: Path, operations: Path | None = None,
                 *, deployed_commit: str | None = None, activation_check=None):
        if not 0 <= port <= 65535:
            raise ValueError("HOPE_DASHBOARD_PORT_INVALID")
        if deployed_commit is not None and not fullmatch(r"[0-9a-f]{40}",deployed_commit):
            raise ValueError("HOPE_DASHBOARD_COMMIT_INVALID")
        self.deployed_commit=deployed_commit
        self.activation_check=activation_check
        self.preview = Path(preview)
        self.snapshot = Path(snapshot)
        self.legacy = self.snapshot.parent / 'legacy-research.json'
        self.operations = Path(operations) if operations is not None else self.snapshot.parent / 'operations.json'
        super().__init__(("127.0.0.1", port), DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer
    protocol_version = "HTTP/1.0"

    def _headers(self, code: int, content_type: str, length: int) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; connect-src 'self'; "
            "style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _respond(self, code: int, payload: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        self._headers(code, content_type, len(payload))
        self.wfile.write(payload)

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        # Reject DNS rebinding: requests must use a literal loopback hostname.
        return host in {
            "127.0.0.1", "localhost",
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._respond(403, b"Forbidden")
            return
        if self.server.activation_check is not None:
            try:self.server.activation_check()
            except Exception:
                self._respond(503,b"Dashboard activation requires recovery")
                return
        if self.path in {"/", "/index.html"}:
            try:
                if self.server.preview.stat().st_size > 65536:
                    raise ValueError("HOPE_DASHBOARD_PAGE_TOO_LARGE")
                payload = self.server.preview.read_bytes()
                if len(payload) > 65536:
                    raise ValueError("HOPE_DASHBOARD_PAGE_TOO_LARGE")
            except (OSError, ValueError):
                self._respond(503, b"Dashboard unavailable")
                return
            self._respond(200, payload, "text/html; charset=utf-8")
            return
        if self.path == "/status.json":
            # Server liveness is independent of the availability of the database.
            # Do not reveal database errors, credentials, host paths or SQL.
            state = "UNAVAILABLE"
            generated_at = None
            try:
                _, generated = _verified_snapshot(self.server.snapshot)
                generated_at = generated.isoformat()
                state = (
                    "STALE" if (datetime.now(timezone.utc) - generated).total_seconds() > 3600
                    else "CURRENT"
                )
            except (OSError, ValueError, TypeError, UnicodeError):
                pass
            payload = json.dumps({
                "schema_version": 1,
                "service": "RUNNING",
                "data_feed": state,
                "snapshot_generated_at": generated_at,
            }, separators=(",", ":")).encode("utf-8")
            self._respond(200, payload, "application/json; charset=utf-8")
            return
        if self.path == "/legacy-research.json":
            try:
                payload, _ = _verified_legacy_inventory(self.server.legacy)
            except (OSError, ValueError, TypeError, UnicodeError):
                self._respond(503, b"Verified legacy research inventory unavailable")
                return
            self._respond(200, payload, "application/json; charset=utf-8")
            return
        if self.path == "/operations.json":
            try:
                payload = _verified_operations(self.server.operations)
            except (OSError, ValueError, TypeError, UnicodeError):
                self._respond(503, b"Verified operations status unavailable")
                return
            self._respond(200, payload, "application/json; charset=utf-8")
            return
        if self.path == "/readiness.json":
            from hope.api.dashboard_readiness import dashboard_readiness
            payload = json.dumps(dashboard_readiness(
                self.server.snapshot, self.server.operations,
                expected_commit=self.server.deployed_commit,
            ),separators=(",",":")).encode("utf-8")
            self._respond(200,payload,"application/json; charset=utf-8")
            return
        if self.path == "/release.json":
            payload=json.dumps({"schema":"hope.dashboard-release.v1",
                "dashboard_commit":self.server.deployed_commit,"paper_only":True,
                "live_enabled":False,"execution_authorized":False},separators=(",",":")).encode()
            self._respond(200 if self.server.deployed_commit else 503,payload,"application/json; charset=utf-8")
            return
        if self.path == "/snapshot.json":
            try:
                payload, _ = _verified_snapshot(self.server.snapshot)
            except (OSError, ValueError, TypeError, UnicodeError):
                self._respond(503, b"Verified snapshot unavailable")
                return
            self._respond(200, payload, "application/json; charset=utf-8")
            return
        self._respond(404, b"Not found")

    def do_POST(self) -> None:
        self._respond(405, b"Read-only dashboard")

    def do_PUT(self) -> None:
        self._respond(405, b"Read-only dashboard")

    def do_DELETE(self) -> None:
        self._respond(405, b"Read-only dashboard")

    def do_HEAD(self) -> None:
        self._respond(405, b"Read-only dashboard")

    def log_message(self, format: str, *args: object) -> None:
        # No user-controlled request path or headers in application logs.
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve HOPE read-only dashboard over loopback")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--operations-status-file", type=Path)
    parser.add_argument("--deployed-commit")
    parser.add_argument("--activation-gate",action="store_true")
    args = parser.parse_args()
    check=None
    if args.activation_gate:
        if args.deployed_commit is None:parser.error("--activation-gate requires --deployed-commit")
        from hope.api.dashboard_activation import Layout,runtime_gate
        check=lambda:runtime_gate(Layout(),args.deployed_commit)
    with DashboardHTTPServer(args.port, args.preview, args.snapshot, args.operations_status_file,
                             deployed_commit=args.deployed_commit,activation_check=check) as server:
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
