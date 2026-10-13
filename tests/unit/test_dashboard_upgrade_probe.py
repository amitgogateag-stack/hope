"""Real and adversarial tests for the private post-upgrade HTTP acceptance probe."""
import io
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest

from hope.api.dashboard_server import DashboardHTTPServer
from hope.api.dashboard_upgrade_probe import verify_dashboard, verify_with_startup_retry


class Response(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status


class FakeOpener:
    def __init__(self, responses=None, *, fail_first=0):
        self.responses = responses or {}
        self.fail_first = fail_first
        self.calls = 0

    def open(self, request, timeout=3):
        self.calls += 1
        if self.fail_first:
            self.fail_first -= 1
            raise URLError("connection refused")
        path = urlsplit(request.full_url).path
        status, body = self.responses.get(path, (503, b"unexpected"))
        if status >= 400:
            raise HTTPError(request.full_url, status, "error", {}, io.BytesIO(body))
        return Response(body, status)


def fixture_responses():
    return {
        "/status.json": (200, json.dumps({
            "schema_version": 1, "service": "RUNNING", "data_feed": "UNAVAILABLE",
        }).encode()),
        "/": (200, b'<div id="legacy-experiments" id="legacy-strategies" id="legacy-datasets">HOPE dashboard v0.5</div>'),
        "/legacy-research.json": (503, b"Verified legacy research inventory unavailable"),
        "/snapshot.json": (503, b"Verified snapshot unavailable"),
    }


def test_probe_accepts_missing_feeds_without_inventing_counts():
    result = verify_dashboard(opener=FakeOpener(fixture_responses()))
    assert result == {"service": "RUNNING", "paper_feed": "UNAVAILABLE", "legacy_feed": "UNAVAILABLE"}


def test_probe_accepts_valid_independent_data_feeds():
    responses = fixture_responses()
    responses["/legacy-research.json"] = (200, json.dumps({
        "schema_version": 1, "source": "hope_legacy_research_inventory",
        "paper": None, "schema_compatible": True,
    }).encode())
    responses["/snapshot.json"] = (200, json.dumps({
        "schema_version": 1, "source": "hope_postgresql",
        "paper": {"environment_state": "HALTED"},
    }).encode())
    assert verify_dashboard(opener=FakeOpener(responses))["legacy_feed"] == "AVAILABLE"
    assert verify_dashboard(opener=FakeOpener(responses))["paper_feed"] == "AVAILABLE"


@pytest.mark.parametrize("path,response", [
    ("/status.json", (200, b"not-json")),
    ("/status.json", (200, b'{"schema_version":true,"service":"RUNNING","data_feed":"UNAVAILABLE"}')),
    ("/status.json", (200, b'{"schema_version":1,"service":"STOPPED","data_feed":"UNAVAILABLE"}')),
    ("/status.json", (302, b"redirect")),
    ("/", (200, b"HOPE dashboard v0.4")),
    ("/", (200, b"x" * 32769)),
    ("/", (503, b"unavailable")),
    ("/legacy-research.json", (200, b'{"source":"hope_postgresql","paper":null}')),
    ("/legacy-research.json", (302, b"redirect")),
    ("/legacy-research.json", (503, b"unexpected")),
    ("/snapshot.json", (200, b'{"source":"hope_legacy_research_inventory","paper":{}}')),
    ("/snapshot.json", (200, b"not-json")),
    ("/snapshot.json", (302, b"redirect")),
    ("/snapshot.json", (503, b"unexpected")),
])
def test_probe_rejects_invalid_release_or_feed(path, response):
    responses = fixture_responses()
    responses[path] = response
    with pytest.raises((ValueError, json.JSONDecodeError)):
        verify_dashboard(opener=FakeOpener(responses))


@pytest.mark.parametrize("base", [
    "https://127.0.0.1:8787", "http://example.com:8787",
    "http://localhost:8787", "http://127.0.0.1:8787/path",
    "http://127.0.0.1:8787/?q=1", "http://user:pass@127.0.0.1:8787",
])
def test_probe_refuses_noncanonical_loopback_urls(base):
    with pytest.raises(ValueError, match="LOOPBACK_ONLY"):
        verify_dashboard(base, opener=FakeOpener(fixture_responses()))


def test_probe_retries_connection_refused_but_not_bad_http(monkeypatch):
    import hope.api.dashboard_upgrade_probe as module
    delays = []
    monkeypatch.setattr(module.time, "sleep", lambda n: delays.append(n))
    opener = FakeOpener(fixture_responses(), fail_first=2)
    assert verify_with_startup_retry(opener=opener, attempts=3, delay=0.1)["service"] == "RUNNING"
    assert len(delays) == 2
    broken = fixture_responses()
    broken["/"] = (200, b"wrong release")
    with pytest.raises(ValueError, match="RELEASE_MISMATCH"):
        verify_with_startup_retry(opener=FakeOpener(broken), attempts=3)
    assert len(delays) == 2


def test_probe_retry_exhaustion_and_invalid_policy(monkeypatch):
    import hope.api.dashboard_upgrade_probe as module
    monkeypatch.setattr(module.time, "sleep", lambda n: None)
    with pytest.raises(URLError):
        verify_with_startup_retry(opener=FakeOpener(fail_first=6), attempts=3, delay=0)
    for attempts, delay in ((0, 1), (11, 1), (3, -1), (3, 6)):
        with pytest.raises(ValueError, match="RETRY_POLICY"):
            verify_with_startup_retry(attempts=attempts, delay=delay)


def test_probe_checks_real_loopback_dashboard_server(tmp_path):
    page = tmp_path / "preview.html"
    page.write_bytes(fixture_responses()["/"][1])
    snapshot = tmp_path / "snapshot.json"
    with DashboardHTTPServer(0, page, snapshot) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = verify_dashboard(f"http://127.0.0.1:{server.server_port}")
            assert result["service"] == "RUNNING"
            assert result["paper_feed"] == "UNAVAILABLE"
            assert result["legacy_feed"] == "UNAVAILABLE"
        finally:
            server.shutdown()
            thread.join(timeout=5)
