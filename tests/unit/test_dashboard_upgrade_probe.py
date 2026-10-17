"""Real and adversarial tests for the private post-upgrade HTTP acceptance probe."""
import io
import json
from datetime import timedelta
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
            "snapshot_generated_at": None,
        }).encode()),
        "/": (200, b'<div id="legacy-experiments" id="legacy-strategies" id="legacy-datasets">HOPE dashboard v0.5</div>'),
        "/legacy-research.json": (503, b"Verified legacy research inventory unavailable"),
        "/snapshot.json": (503, b"Verified snapshot unavailable"),
    }


def test_m2_page_budget_is_64k_while_snapshot_budget_stays_32k():
    responses = fixture_responses()
    responses["/"] = (200, responses["/"][1] + b" " * 40000)
    assert verify_dashboard(opener=FakeOpener(responses))["service"] == "RUNNING"
    responses["/"] = (200, responses["/"][1] + b" " * 65536)
    with pytest.raises(ValueError, match="OVERSIZED_HTTP_RESPONSE"):
        verify_dashboard(opener=FakeOpener(responses))
    responses = fixture_responses()
    responses["/snapshot.json"] = (200, b"x" * 32769)
    with pytest.raises(ValueError, match="OVERSIZED_HTTP_RESPONSE"):
        verify_dashboard(opener=FakeOpener(responses))



def valid_legacy():
    return {
        "schema_version": 1, "source": "hope_legacy_research_inventory",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_compatible": True, "paper": None,
        "research": {
            "registered_experiments": 7, "registered_strategy_versions": 3,
            "registered_datasets": 2,
        },
    }


def valid_paper():
    return {
        "schema_version": 1, "source": "hope_postgresql",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research": {"run_count": 7},
        "paper": {
            "environment_state": "HALTED",
            "job_runs": {"total": 2, "claimed": 0, "succeeded": 1, "failed": 1},
        },
    }


def test_probe_accepts_missing_feeds_without_inventing_counts():
    result = verify_dashboard(opener=FakeOpener(fixture_responses()))
    assert result == {"service": "RUNNING", "paper_feed": "UNAVAILABLE", "legacy_feed": "UNAVAILABLE"}


def test_probe_accepts_valid_independent_data_feeds():
    responses = fixture_responses()
    responses["/legacy-research.json"] = (200, json.dumps(valid_legacy()).encode())
    responses["/snapshot.json"] = (200, json.dumps(valid_paper()).encode())
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



@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(generated_at="not-a-timestamp"),
    lambda d: d.update(generated_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
    lambda d: d.update(database_password="DO_NOT_LEAK"),
    lambda d: d["research"].update(run_count=True),
    lambda d: d["research"].update(run_count=9007199254740992),
    lambda d: d["paper"].update(environment_state="LIVE"),
    lambda d: d["paper"].update(broker_credentials="DO_NOT_LEAK"),
    lambda d: d["paper"]["job_runs"].update(total=99),
    lambda d: d["paper"]["job_runs"].update(claimed=-1),
    lambda d: d["paper"]["job_runs"].update(failed=9007199254740992),
    lambda d: d["paper"]["job_runs"].update(secret_orders=["DO_NOT_LEAK"]),
])
def test_probe_rejects_invalid_or_unsanitized_paper_response(mutate):
    responses = fixture_responses()
    payload = valid_paper()
    mutate(payload)
    responses["/snapshot.json"] = (200, json.dumps(payload).encode())
    with pytest.raises(ValueError, match="HOPE_UPGRADE_PAPER"):
        verify_dashboard(opener=FakeOpener(responses))


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(generated_at="not-a-timestamp"),
    lambda d: d.update(generated_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
    lambda d: d.update(database_password="DO_NOT_LEAK"),
    lambda d: d.update(paper={"job_runs": {"total": 1}}),
    lambda d: d["research"].update(registered_experiments=True),
    lambda d: d["research"].update(registered_datasets=-1),
    lambda d: d["research"].update(registered_strategy_versions=9007199254740992),
    lambda d: d["research"].update(secret="DO_NOT_LEAK"),
])
def test_probe_rejects_invalid_or_unsanitized_legacy_response(mutate):
    responses = fixture_responses()
    payload = valid_legacy()
    mutate(payload)
    responses["/legacy-research.json"] = (200, json.dumps(payload).encode())
    with pytest.raises(ValueError, match="HOPE_UPGRADE_LEGACY"):
        verify_dashboard(opener=FakeOpener(responses))


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra="DO_NOT_LEAK"),
    lambda d: d.update(snapshot_generated_at="not-a-timestamp"),
    lambda d: d.update(data_feed="CURRENT"),
])
def test_probe_rejects_inconsistent_or_unsanitized_health_response(mutate):
    responses = fixture_responses()
    payload = json.loads(responses["/status.json"][1])
    mutate(payload)
    responses["/status.json"] = (200, json.dumps(payload).encode())
    with pytest.raises(ValueError, match="HOPE_UPGRADE_HEALTH_INVALID"):
        verify_dashboard(opener=FakeOpener(responses))


@pytest.mark.parametrize("with_recovery", [False, True], ids=["original", "recovery"])
def test_probe_accepts_real_sanitized_paper_and_legacy_server_responses(tmp_path, with_recovery):
    page = tmp_path / "preview.html"
    page.write_bytes(fixture_responses()["/"][1])
    snapshot = tmp_path / "snapshot.json"
    payload = valid_paper()
    if with_recovery:
        payload["paper"]["job_runs"] = {"total": 4, "claimed": 2, "succeeded": 1, "failed": 1}
        payload["paper"]["recovery"] = {
            "claimed_with_effects": 1, "claimed_without_effects": 1,
            "claims_older_than_one_hour": 2, "reconciliation_receipts": 1,
        }
    snapshot.write_text(json.dumps(payload))
    legacy = tmp_path / "legacy-research.json"
    # The source inventory contains missing_columns; the server intentionally strips it.
    legacy.write_text(json.dumps({**valid_legacy(), "missing_columns": {}}))
    with DashboardHTTPServer(0, page, snapshot) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = verify_dashboard(f"http://127.0.0.1:{server.server_port}")
            assert result == {
                "service": "RUNNING", "paper_feed": "AVAILABLE", "legacy_feed": "AVAILABLE",
            }
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("change", [
    {"claimed_with_effects": True}, {"claimed_without_effects": -1},
    {"claimed_with_effects": 9007199254740992},
    {"claims_older_than_one_hour": 3}, {"reconciliation_receipts": 2},
    {"claimed_without_effects": 2}, {"credentials": "DO_NOT_LEAK"},
])
def test_probe_rejects_corrupt_or_unsanitized_recovery_http_response(change):
    payload = valid_paper()
    payload["paper"]["job_runs"] = {"total": 4, "claimed": 2, "succeeded": 1, "failed": 1}
    payload["paper"]["recovery"] = {
        "claimed_with_effects": 1, "claimed_without_effects": 1,
        "claims_older_than_one_hour": 2, "reconciliation_receipts": 1, **change,
    }
    responses = fixture_responses()
    responses["/snapshot.json"] = (200, json.dumps(payload).encode())
    with pytest.raises(ValueError, match="HOPE_UPGRADE_PAPER_RECOVERY"):
        verify_dashboard(opener=FakeOpener(responses))


@pytest.mark.parametrize("recovery", [None, [], {}, "private"])
def test_probe_rejects_partial_or_wrong_shape_recovery_http_response(recovery):
    payload = valid_paper()
    payload["paper"]["recovery"] = recovery
    responses = fixture_responses()
    responses["/snapshot.json"] = (200, json.dumps(payload).encode())
    with pytest.raises(ValueError, match="HOPE_UPGRADE_PAPER_RECOVERY_INVALID"):
        verify_dashboard(opener=FakeOpener(responses))
