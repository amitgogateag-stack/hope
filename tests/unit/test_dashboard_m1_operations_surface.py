"""Optional service heartbeat cannot expose credentials or imply LIVE readiness."""
from datetime import datetime, timedelta, timezone
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest

from hope.api.dashboard_server import DashboardHTTPServer, _verified_operations


def health():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1, "generated_at": now, "state": "CURRENT",
        "last_success_at": now, "cycles_succeeded": 1, "cycles_failed": 0,
        "jobs_executed": 0, "jobs_skipped": 0, "paper_only": True,
        "live_enabled": False, "error_code": None,
        "deployed_commit": "a" * 40, "schema_compatibility": "COMPATIBLE",
    }


def test_operations_projection_whitelists_fields_and_normalizes_unknown_error(tmp_path):
    path = tmp_path / "operations.json"
    value = health()
    value.update(credentials="NEVER_EXPOSE", job_ids=["sensitive"], error_code="NEVER_EXPOSE")
    path.write_text(json.dumps(value))
    safe = json.loads(_verified_operations(path))
    assert set(safe) == set(health())
    assert safe["error_code"] == "PAPER_SERVICE_CYCLE_FAILED"
    assert "NEVER_EXPOSE" not in json.dumps(safe) and "sensitive" not in json.dumps(safe)
    assert safe["jobs_executed"] == 0


def test_operations_projection_rejects_heartbeat_from_other_expected_release(tmp_path):
    path = tmp_path / "operations.json"
    path.write_text(json.dumps(health()))
    with pytest.raises(ValueError, match="HOPE_OPERATIONS_COMMIT_MISMATCH"):
        _verified_operations(path, expected_commit="b" * 40)


@pytest.mark.parametrize("code", ["PAPER_SERVICE_CERTIFIED_BINDING_REQUIRED", "PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE", "PAPER_STRATEGY_AUTHORIZATION_RESEARCH_INVALID", "PAPER_CERTIFIED_INPUT_UNAVAILABLE"])
def test_m2_certified_admission_error_codes_are_preserved_without_raw_errors(tmp_path, code):
    path = tmp_path / "operations.json"
    path.write_text(json.dumps({**health(), "state": "UNAVAILABLE", "error_code": code}))
    assert json.loads(_verified_operations(path))["error_code"] == code


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"paper_only": False}, {"live_enabled": True},
    {"state": "LIVE"}, {"jobs_executed": True}, {"jobs_skipped": -1},
    {"cycles_failed": 9007199254740992}, {"cycles_succeeded": 1.5},
    {"generated_at": "2026-10-16T12:00:00"}, {"generated_at": "invalid"},
    {"generated_at": timedelta(hours=1)},
    {"last_success_at": None}, {"last_success_at": timedelta(minutes=1)},
    {"deployed_commit": "credentials"}, {"deployed_commit": True},
    {"schema_compatibility": "LIVE_READY"},
])
def test_operations_projection_rejects_invalid_or_contradictory_heartbeat(tmp_path, change):
    path = tmp_path / "operations.json"
    # Construct relative clocks at execution, not collection: a full PostgreSQL
    # run may take longer than the future offset before this case is reached.
    change = {key: (datetime.now(timezone.utc) + value).isoformat() if isinstance(value, timedelta) else value
              for key, value in change.items()}
    path.write_text(json.dumps({**health(), **change}))
    with pytest.raises(ValueError):
        _verified_operations(path)


@pytest.mark.parametrize("state", ["STARTING", "HALTED", "UNAVAILABLE", "STOPPED"])
def test_operations_noncurrent_state_without_success_is_explicit(tmp_path, state):
    path = tmp_path / "operations.json"
    path.write_text(json.dumps({**health(), "state": state, "last_success_at": None}))
    assert json.loads(_verified_operations(path))["state"] == state


@pytest.mark.parametrize("raw", [b"partial{", b"[]", b"x" * 8193])
def test_operations_partial_or_oversized_file_fails_closed(tmp_path, raw):
    path = tmp_path / "operations.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        _verified_operations(path)


def test_real_operations_endpoint_is_optional_sanitized_and_read_only(tmp_path):
    page = tmp_path / "preview.html"
    page.write_text("HOPE")
    operations = tmp_path / "private" / "health.json"
    operations.parent.mkdir()
    opener = build_opener(ProxyHandler({}))
    with DashboardHTTPServer(0, page, tmp_path / "snapshot.json", operations) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with pytest.raises(HTTPError) as error:
                opener.open(base + "/operations.json")
            assert error.value.code == 503
            assert error.value.read() == b"Verified operations status unavailable"
            error.value.close()
            operations.write_text(json.dumps({**health(), "password": "NEVER_EXPOSE"}))
            with opener.open(base + "/operations.json") as response:
                body = response.read()
                assert response.status == 200 and b"NEVER_EXPOSE" not in body
                assert response.headers["Cache-Control"].startswith("no-store")
                assert json.loads(body)["live_enabled"] is False
            # Corruption must not turn optional health into service liveness loss.
            operations.write_text("partial{")
            with opener.open(base + "/status.json") as response:
                assert json.loads(response.read())["service"] == "RUNNING"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_real_operations_endpoint_rejects_cross_release_heartbeat(tmp_path):
    page = tmp_path / "preview.html"
    page.write_text("HOPE")
    operations = tmp_path / "health.json"
    operations.write_text(json.dumps(health()))
    opener = build_opener(ProxyHandler({}))
    with DashboardHTTPServer(
        0, page, tmp_path / "snapshot.json", operations,
        paper_deployed_commit="b" * 40,
    ) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(HTTPError) as error:
                opener.open(f"http://127.0.0.1:{server.server_port}/operations.json")
            assert error.value.code == 503
            error.value.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
