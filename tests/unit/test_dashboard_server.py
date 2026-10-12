"""Private dashboard HTTP surface: loopback, read-only and no path traversal."""
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from hope.api.dashboard_server import DashboardHTTPServer


@contextmanager
def dashboard(tmp_path: Path, snapshot: bytes | None = None):
    preview = tmp_path / "preview.html"
    preview.write_text("<h1>HOPE</h1>", encoding="utf-8")
    output = tmp_path / "snapshot.json"
    if snapshot is not None:
        output.write_bytes(snapshot)
    with DashboardHTTPServer(0, preview, output) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


def request(server, path: str, *, method: str = "GET", host: str | None = None):
    req = Request(f"http://127.0.0.1:{server.server_port}" + path, method=method)
    if host is not None:
        req.add_header("Host", host)
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_server_is_loopback_only_and_serves_private_preview(tmp_path):
    with dashboard(tmp_path) as server:
        assert server.server_address[0] == "127.0.0.1"
        status, headers, body = request(server, "/")
        assert status == 200 and body == b"<h1>HOPE</h1>"
        assert headers["Cache-Control"].startswith("no-store")
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "default-src 'none'" in headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/snapshot.json", "/index.html", "/../secrets", "/?x=1", "/unknown"])
def test_server_missing_snapshot_or_unknown_paths_fail_closed(tmp_path, path):
    with dashboard(tmp_path) as server:
        status, _, _ = request(server, path)
        assert status == (503 if path == "/snapshot.json" else 200 if path == "/index.html" else 404)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "HEAD"])
def test_server_refuses_all_write_methods(tmp_path, method):
    with dashboard(tmp_path) as server:
        status, _, _ = request(server, "/", method=method)
        assert status == 405


def test_server_rejects_dns_rebinding_host(tmp_path):
    with dashboard(tmp_path) as server:
        status, _, _ = request(server, "/", host="evil.example")
        assert status == 403


@pytest.mark.parametrize("snapshot,expected", [
    (b"not json", 503),
    (b'{"schema_version":1,"source":"unknown"}', 503),
    (b"x" * 32769, 503),
    (json.dumps({"schema_version": 1, "source": "hope_postgresql", "generated_at": "2026-10-12T00:00:00Z"}).encode(), 200),
])
def test_server_snapshot_schema_and_size_boundary(tmp_path, snapshot, expected):
    with dashboard(tmp_path, snapshot) as server:
        status, headers, body = request(server, "/snapshot.json")
        assert status == expected
        if status == 200:
            assert headers["Content-Type"].startswith("application/json")
            assert body == snapshot
