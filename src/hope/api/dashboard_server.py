"""Loopback-only read-only HOPE dashboard HTTP server.

Use an authenticated SSH tunnel to reach it; never expose this port publicly.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, preview: Path, snapshot: Path):
        if not 0 <= port <= 65535:
            raise ValueError("HOPE_DASHBOARD_PORT_INVALID")
        self.preview = Path(preview)
        self.snapshot = Path(snapshot)
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
        if self.path == "/snapshot.json":
            try:
                payload = self.server.snapshot.read_bytes()
                if len(payload) > 32768:
                    raise ValueError("Snapshot exceeds size limit")
                data = json.loads(payload)
                if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("source") != "hope_postgresql":
                    raise ValueError("Unrecognized snapshot")
            except (OSError, ValueError, UnicodeError):
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
