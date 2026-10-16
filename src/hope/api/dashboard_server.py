"""Loopback-only read-only HOPE dashboard HTTP server.

Use an authenticated SSH tunnel to reach it; never expose this port publicly.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


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
    if recovery is not None:
        safe["paper"]["recovery"] = {key: recovery[key] for key in recovery_fields}
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


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, preview: Path, snapshot: Path):
        if not 0 <= port <= 65535:
            raise ValueError("HOPE_DASHBOARD_PORT_INVALID")
        self.preview = Path(preview)
        self.snapshot = Path(snapshot)
        self.legacy = self.snapshot.parent / 'legacy-research.json'
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
        if self.path in {"/", "/index.html"}:
            try:
                payload = self.server.preview.read_bytes()
            except OSError:
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
    args = parser.parse_args()
    with DashboardHTTPServer(args.port, args.preview, args.snapshot) as server:
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
