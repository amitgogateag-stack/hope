"""Loopback-only read-only HOPE dashboard HTTP server.

Use an authenticated SSH tunnel to reach it; never expose this port publicly.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from re import fullmatch
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import SHUT_RDWR
from threading import BoundedSemaphore, Lock, Timer

from hope.api.dashboard_projection import verified_projections
from hope.api.snapshot_contract import verified_snapshot_payload
from hope.infrastructure.durable_file import bounded_regular_read

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


def _verified_snapshot(path: Path, *, expected_commit: str | None = None) -> tuple[bytes, datetime]:
    return verified_snapshot_payload(bounded_regular_read(path,32768),expected_commit=expected_commit)


def _verified_legacy_inventory(path: Path) -> tuple[bytes, datetime]:
    """Return only a sanitized legacy registration inventory, never PAPER data."""
    raw = bounded_regular_read(path, 8192)
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


def _verified_operations(path: Path, *, expected_commit: str | None = None) -> bytes:
    """Sanitize an optional local service heartbeat, never expose exception text."""
    raw = bounded_regular_read(path, 8192)
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
    if expected_commit is not None and commit != expected_commit:
        raise ValueError("HOPE_OPERATIONS_COMMIT_MISMATCH")
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
                 *, deployed_commit: str | None = None, paper_deployed_commit: str | None = None,
                 activation_check=None, activation_view=None,
                 client_io_timeout_seconds: float = 5.0,
                 client_total_timeout_seconds: float = 15.0,
                 max_concurrent_clients: int = 32):
        if not 0 <= port <= 65535:
            raise ValueError("HOPE_DASHBOARD_PORT_INVALID")
        if (isinstance(client_io_timeout_seconds, bool)
                or not isinstance(client_io_timeout_seconds, (int, float))
                or not 0 < client_io_timeout_seconds <= 60):
            raise ValueError("HOPE_DASHBOARD_CLIENT_TIMEOUT_INVALID")
        if (isinstance(client_total_timeout_seconds, bool)
                or not isinstance(client_total_timeout_seconds, (int, float))
                or not 0 < client_total_timeout_seconds <= 300):
            raise ValueError("HOPE_DASHBOARD_CLIENT_TOTAL_TIMEOUT_INVALID")
        if type(max_concurrent_clients) is not int or not 1 <= max_concurrent_clients <= 256:
            raise ValueError("HOPE_DASHBOARD_CLIENT_LIMIT_INVALID")
        if deployed_commit is not None and not fullmatch(r"[0-9a-f]{40}",deployed_commit):
            raise ValueError("HOPE_DASHBOARD_COMMIT_INVALID")
        if paper_deployed_commit is not None and not fullmatch(r"[0-9a-f]{40}",paper_deployed_commit):
            raise ValueError("HOPE_PAPER_COMMIT_INVALID")
        self.deployed_commit=deployed_commit
        self.paper_deployed_commit=paper_deployed_commit
        self.activation_check=activation_check
        self.activation_view=activation_view
        self.client_io_timeout_seconds=float(client_io_timeout_seconds)
        self.client_total_timeout_seconds=float(client_total_timeout_seconds)
        self.max_concurrent_clients=max_concurrent_clients
        self._client_slots=BoundedSemaphore(max_concurrent_clients)
        self._transport_lock=Lock()
        self._active_clients=0
        self._rejected_clients_total=0
        self._expired_clients_total=0
        self.preview = Path(preview)
        self.snapshot = Path(snapshot)
        self.legacy = self.snapshot.parent / 'legacy-research.json'
        self.operations = Path(operations) if operations is not None else self.snapshot.parent / 'operations.json'
        super().__init__(("127.0.0.1", port), DashboardHandler)

    def get_request(self):
        """Bound client header and response I/O, including activation leases."""
        request,address=super().get_request()
        try:
            request.settimeout(self.client_io_timeout_seconds)
        except BaseException:
            request.close()
            raise
        return request,address

    def process_request(self, request, client_address):
        """Reject excess clients before allocating another handler thread."""
        if not self._client_slots.acquire(blocking=False):
            self._change_transport(rejected=1)
            self.shutdown_request(request)
            return
        self._change_transport(active=1)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._change_transport(active=-1)
            self._client_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        lifecycle_lock=Lock()
        lifecycle={"finished":False}
        deadline=Timer(self.client_total_timeout_seconds,self._expire_request,
            args=(request,lifecycle_lock,lifecycle))
        deadline.daemon=True
        try:
            deadline.start()
            super().process_request_thread(request, client_address)
        finally:
            with lifecycle_lock:lifecycle["finished"]=True
            deadline.cancel()
            self._change_transport(active=-1)
            self._client_slots.release()

    def _expire_request(self,request,lifecycle_lock,lifecycle):
        """Interrupt all client I/O when the total wall-clock budget expires."""
        with lifecycle_lock:
            if lifecycle["finished"]:return
            self._change_transport(expired=1)
        try:
            request.shutdown(SHUT_RDWR)
        except OSError:
            pass

    def _change_transport(self,*,active=0,rejected=0,expired=0):
        with self._transport_lock:
            self._active_clients+=active
            self._rejected_clients_total+=rejected
            self._expired_clients_total+=expired

    def transport_status(self):
        """Return a consistent, aggregate-only transport observation."""
        with self._transport_lock:
            return {
                "active_clients":self._active_clients,
                "max_concurrent_clients":self.max_concurrent_clients,
                "rejected_clients_total":self._rejected_clients_total,
                "expired_clients_total":self._expired_clients_total,
            }


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
        stack=ExitStack()
        try:
            if self.server.activation_view is not None:
                stack.enter_context(self.server.activation_view())
            elif self.server.activation_check is not None:
                self.server.activation_check()
        except Exception:
            stack.close()
            self._respond(503,b"Dashboard activation requires recovery")
            return
        try:
            with stack:self._dispatch_GET()
        except (BrokenPipeError,ConnectionResetError,TimeoutError):
            # A local client cannot retain an activation lease by abandoning or
            # indefinitely stalling a response. The context exits before return.
            return

    def _dispatch_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            try:
                payload = bounded_regular_read(self.server.preview, 65536)
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
                _, generated = _verified_snapshot(
                    self.server.snapshot, expected_commit=self.server.deployed_commit,
                )
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
        if self.path == "/transport.json":
            payload=json.dumps({
                "schema_version":1,**self.server.transport_status(),
            },separators=(",", ":")).encode("utf-8")
            self._respond(200,payload,"application/json; charset=utf-8")
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
                payload = _verified_operations(
                    self.server.operations, expected_commit=self.server.paper_deployed_commit,
                )
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
                expected_paper_commit=self.server.paper_deployed_commit,
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
                payload, _ = _verified_snapshot(
                    self.server.snapshot, expected_commit=self.server.deployed_commit,
                )
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
    paper_deployed_commit=None
    if args.activation_gate:
        if args.deployed_commit is None:parser.error("--activation-gate requires --deployed-commit")
        from hope.api.dashboard_activation import Layout,runtime_gate,runtime_view
        layout=Layout()
        paper_deployed_commit=runtime_gate(layout,args.deployed_commit)["paper_commit"]
        view=lambda:runtime_view(layout,args.deployed_commit)
    else:view=None
    with DashboardHTTPServer(args.port, args.preview, args.snapshot, args.operations_status_file,
                             deployed_commit=args.deployed_commit,
                             paper_deployed_commit=paper_deployed_commit,
                             activation_view=view) as server:
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
