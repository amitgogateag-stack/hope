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


def recovery_snapshot(**updates):
    data = json.loads(valid_snapshot())
    data["paper"]["job_runs"] = {"total": 4, "claimed": 2, "succeeded": 1, "failed": 1}
    data["paper"]["recovery"] = {
        "claimed_with_effects": 1, "claimed_without_effects": 1,
        "claims_older_than_one_hour": 2, "reconciliation_receipts": 1,
        **updates,
    }
    return json.dumps(data).encode()


def test_server_serves_whitelisted_recovery_observations_only(tmp_path):
    with dashboard(tmp_path, recovery_snapshot(credentials="DO_NOT_SERVE", job_ids=["private"])) as server:
        status, _, body = request(server, "/snapshot.json")
        assert status == 200
        recovery = json.loads(body)["paper"]["recovery"]
        assert recovery == {
            "claimed_with_effects": 1, "claimed_without_effects": 1,
            "claims_older_than_one_hour": 2, "reconciliation_receipts": 1,
        }
        assert b"DO_NOT_SERVE" not in body and b"private" not in body


@pytest.mark.parametrize("updates", [
    {"claimed_with_effects": 3}, {"claimed_without_effects": -1},
    {"claims_older_than_one_hour": 3}, {"reconciliation_receipts": 2},
    {"claimed_with_effects": True}, {"claimed_without_effects": None},
    {"reconciliation_receipts": 9007199254740992},
])
def test_server_refuses_corrupt_recovery_observations(tmp_path, updates):
    with dashboard(tmp_path, recovery_snapshot(**updates)) as server:
        status, _, body = request(server, "/snapshot.json")
        assert status == 503 and body == b"Verified snapshot unavailable"
        _, _, status_body = request(server, "/status.json")
        assert json.loads(status_body)["data_feed"] == "UNAVAILABLE"


@pytest.mark.parametrize("invalid", [None, [], "private"])
def test_server_rejects_recovery_field_with_wrong_shape(tmp_path, invalid):
    data = json.loads(valid_snapshot())
    data["paper"]["recovery"] = invalid
    with dashboard(tmp_path, json.dumps(data).encode()) as server:
        assert request(server, "/snapshot.json")[0] == 503


@pytest.mark.parametrize("snapshot,expected", [
    (b"not json", 503),
    (b'{"schema_version":1,"source":"unknown"}', 503),
    (b"x" * 32769, 503),
    (b'{"schema_version":1,"source":"hope_postgresql"}', 503),
    (valid_snapshot(), 200),
], ids=["malformed", "wrong-source", "oversized", "incomplete", "valid"])
def test_server_snapshot_schema_and_size_boundary(tmp_path, snapshot, expected):
    with dashboard(tmp_path, snapshot) as server:
        status, headers, body = request(server, "/snapshot.json")
        assert status == expected
        if status == 200:
            assert headers["Content-Type"].startswith("application/json")
            assert json.loads(body) == json.loads(snapshot)


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


def valid_legacy_inventory(*, timestamp=None):
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "source": "hope_legacy_research_inventory",
        "schema_compatible": True,
        "generated_at": timestamp,
        "missing_columns": {},
        "research": {
            "registered_experiments": 7,
            "registered_strategy_versions": 3,
            "registered_datasets": 2,
        },
        "paper": None,
    }


def test_legacy_inventory_is_independent_of_missing_paper_snapshot(tmp_path):
    with dashboard(tmp_path) as server:
        server.legacy.write_text(json.dumps(valid_legacy_inventory()))
        code, headers, body = request(server, "/legacy-research.json")
        assert code == 200
        assert headers["Content-Type"].startswith("application/json")
        result = json.loads(body)
        assert result["research"]["registered_experiments"] == 7
        assert result["paper"] is None
        assert result["source"] != "hope_postgresql"
        assert request(server, "/snapshot.json")[0] == 503


@pytest.mark.parametrize("change", [
    lambda d: d.update(source="hope_postgresql"),
    lambda d: d.update(schema_compatible=False),
    lambda d: d.update(paper={"environment_state": "RUNNING"}),
    lambda d: d["research"].update(registered_experiments=-1),
    lambda d: d["research"].update(registered_datasets=True),
    lambda d: d["research"].update(secret="not-for-display"),
    lambda d: d.update(missing_columns={"experiments": ["id"]}),
    lambda d: d.update(generated_at="2026-10-12T00:00:00"),
    lambda d: d.update(generated_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()),
])
def test_legacy_inventory_rejects_invalid_or_sensitive_data(tmp_path, change):
    payload = valid_legacy_inventory()
    change(payload)
    with dashboard(tmp_path) as server:
        server.legacy.write_text(json.dumps(payload))
        assert request(server, "/legacy-research.json")[0] == 503


def test_legacy_inventory_rejects_oversized_data_and_write_methods(tmp_path):
    with dashboard(tmp_path) as server:
        assert request(server, "/legacy-research.json")[0] == 503
        server.legacy.write_bytes(b"x" * 8193)
        assert request(server, "/legacy-research.json")[0] == 503
        assert request(server, "/legacy-research.json", method="POST")[0] == 405
        assert request(server, "/legacy-research.json", host="evil.example")[0] == 403


def test_legacy_inventory_whitelists_fields(tmp_path):
    payload = valid_legacy_inventory()
    payload["unexpected_private_field"] = "must never be served"
    with dashboard(tmp_path) as server:
        server.legacy.write_text(json.dumps(payload))
        code, _, body = request(server, "/legacy-research.json")
        assert code == 200
        assert b"unexpected_private_field" not in body
        assert b"must never be served" not in body



def test_snapshot_endpoint_whitelists_all_fields_even_if_file_contains_secrets(tmp_path):
    payload = json.loads(valid_snapshot())
    payload["database_password"] = "NEVER_SERVE_THIS"
    payload["private_instrument_ids"] = ["sensitive-id"]
    payload["research"]["experiment_details"] = [{"api_key": "NEVER_SERVE_THIS"}]
    payload["paper"]["broker_credentials"] = "NEVER_SERVE_THIS"
    payload["paper"]["job_runs"]["order_ids"] = ["sensitive-order"]
    with dashboard(tmp_path, json.dumps(payload).encode("utf-8")) as server:
        status, headers, body = request(server, "/snapshot.json")
        assert status == 200
        assert headers["Cache-Control"].startswith("no-store")
        assert b"NEVER_SERVE_THIS" not in body
        assert b"sensitive-id" not in body
        assert b"sensitive-order" not in body
        safe = json.loads(body)
        assert set(safe) == {"schema_version", "source", "generated_at", "research", "paper"}
        assert set(safe["research"]) == {"run_count"}
        assert set(safe["paper"]) == {"environment_state", "job_runs"}
        assert set(safe["paper"]["job_runs"]) == {"total", "claimed", "succeeded", "failed"}
        assert safe["paper"]["job_runs"]["total"] == 2


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version=True),
    lambda d: d["research"].update(run_count=9007199254740992),
    lambda d: d["paper"]["job_runs"].update(total=9007199254740992),
    lambda d: d["paper"]["job_runs"].update(failed=9007199254740992),
])
def test_snapshot_rejects_unsafe_browser_counts_and_boolean_schema_version(tmp_path, mutate):
    data = json.loads(valid_snapshot())
    mutate(data)
    with dashboard(tmp_path, json.dumps(data).encode("utf-8")) as server:
        assert request(server, "/snapshot.json")[0] == 503
        code, _, body = request(server, "/status.json")
        assert code == 200
        assert json.loads(body)["data_feed"] == "UNAVAILABLE"


def test_legacy_rejects_boolean_schema_version(tmp_path):
    payload = valid_legacy_inventory()
    payload["schema_version"] = True
    with dashboard(tmp_path) as server:
        server.legacy.write_text(json.dumps(payload))
        assert request(server, "/legacy-research.json")[0] == 503
