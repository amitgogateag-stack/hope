"""Private dashboard HTTP surface: loopback, read-only and no path traversal."""
import json
from datetime import datetime, timedelta, timezone
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


def valid_snapshot(*, generated_at=None):
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()
    return json.dumps({
        "schema_version": 1, "source": "hope_postgresql",
        "generated_at": generated_at,
        "research": {"run_count": 0},
        "paper": {
            "environment_state": "HALTED",
            "job_runs": {"total": 2, "claimed": 0, "succeeded": 1, "failed": 1},
        },
    }).encode("utf-8")


@pytest.mark.parametrize("snapshot,expected", [
    (b"not json", 503),
    (b'{"schema_version":1,"source":"unknown"}', 503),
    (b"x" * 32769, 503),
    (b'{"schema_version":1,"source":"hope_postgresql"}', 503),
    (valid_snapshot(), 200),
])
def test_server_snapshot_schema_and_size_boundary(tmp_path, snapshot, expected):
    with dashboard(tmp_path, snapshot) as server:
        status, headers, body = request(server, "/snapshot.json")
        assert status == expected
        if status == 200:
            assert headers["Content-Type"].startswith("application/json")
            assert body == snapshot


def test_server_reports_liveness_separately_from_missing_data(tmp_path):
    with dashboard(tmp_path) as server:
        status, headers, body = request(server, "/status.json")
        assert status == 200
        assert headers["Cache-Control"].startswith("no-store")
        assert json.loads(body) == {
            "schema_version": 1, "service": "RUNNING",
            "data_feed": "UNAVAILABLE", "snapshot_generated_at": None,
        }


def test_server_reports_current_snapshot_and_never_exposes_db_details(tmp_path):
    with dashboard(tmp_path, valid_snapshot()) as server:
        status, _, body = request(server, "/status.json")
        assert status == 200
        state = json.loads(body)
        assert state["service"] == "RUNNING"
        assert state["data_feed"] == "CURRENT"
        assert state["snapshot_generated_at"]
        assert set(state) == {"schema_version", "service", "data_feed", "snapshot_generated_at"}


def test_server_marks_old_snapshot_stale(tmp_path):
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with dashboard(tmp_path, valid_snapshot(generated_at=timestamp)) as server:
        status, _, body = request(server, "/status.json")
        assert status == 200
        assert json.loads(body)["data_feed"] == "STALE"


@pytest.mark.parametrize("invalid", [
    lambda d: d.update(research={"run_count": -1}),
    lambda d: d["paper"].update(environment_state="UNKNOWN"),
    lambda d: d["paper"]["job_runs"].update(total=999),
    lambda d: d["paper"]["job_runs"].update(claimed=True),
    lambda d: d.update(generated_at="not-a-date"),
    lambda d: d.update(generated_at="2026-10-12T00:00:00"),
    lambda d: d.update(generated_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()),
])
def test_invalid_snapshots_fail_closed_for_both_endpoints(tmp_path, invalid):
    data = json.loads(valid_snapshot())
    invalid(data)
    with dashboard(tmp_path, json.dumps(data).encode("utf-8")) as server:
        status, _, _ = request(server, "/snapshot.json")
        assert status == 503
        status, _, body = request(server, "/status.json")
        assert status == 200
        assert json.loads(body)["data_feed"] == "UNAVAILABLE"


def test_health_endpoint_is_read_only_and_rejects_wrong_host(tmp_path):
    with dashboard(tmp_path, valid_snapshot()) as server:
        status, _, _ = request(server, "/status.json", method="POST")
        assert status == 405
        status, _, _ = request(server, "/status.json", host="evil.example")
        assert status == 403
