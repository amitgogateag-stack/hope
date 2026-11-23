"""Private dashboard HTTP surface: loopback, read-only and no path traversal."""
import json
import hashlib
import os
import socket
import time
from datetime import datetime, timedelta, timezone
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from unittest.mock import Mock

import pytest

from hope.api import dashboard_server as module
from hope.api.dashboard_server import DashboardHTTPServer


@contextmanager
def dashboard(tmp_path: Path, snapshot: bytes | None = None, *, deployed_commit: str | None = None):
    preview = tmp_path / "preview.html"
    preview.write_text("<h1>HOPE</h1>", encoding="utf-8")
    output = tmp_path / "snapshot.json"
    if snapshot is not None:
        output.write_bytes(snapshot)
    with DashboardHTTPServer(0, preview, output, deployed_commit=deployed_commit) as server:
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


def test_acceptance_endpoint_never_promotes_unauthenticated_local_result(tmp_path):
    (tmp_path/"acceptance-result.json").write_text(json.dumps({
        "schema":"hope.operational-acceptance.v1","scope":"DISPOSABLE_LAB_ONLY",
        "state":"VERIFIED","release_commit":"a"*40,
        "generated_at":datetime.now(timezone.utc).isoformat(),"blockers":[],
        "execution_authorized":False,
    }))
    with dashboard(tmp_path) as server:
        status,_,body=request(server,"/acceptance.json")
        result=json.loads(body)
        assert status==200 and result["state"]=="UNVERIFIED"
        assert result["execution_authorized"] is False


def test_rehearsal_endpoint_never_promotes_unauthenticated_local_result(tmp_path):
    from test_m3_dashboard_readiness import verified_runtime
    (tmp_path/"rehearsal-result.json").write_text(json.dumps({
        "schema":"hope.deployment-rehearsal.v1","scope":"DISPOSABLE_LAB_ONLY",
        "state":"UNVERIFIED","lab_result":"COMPLETED","independent_acceptance":"UNVERIFIED",
        "release_commit":"a"*40,"previous_commit":"b"*40,
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "runtime_integrity":verified_runtime(),"execution_authorized":False,
        "host_changes":False,
    }))
    with dashboard(tmp_path) as server:
        status,_,body=request(server,"/rehearsal.json")
        result=json.loads(body)
        assert status==200 and result["state"]=="UNVERIFIED"
        assert result["lab_result"] is None
        assert result["independent_acceptance"]=="UNVERIFIED"
        assert result["execution_authorized"] is False


def test_activation_view_is_held_across_local_file_read_and_response(tmp_path,monkeypatch):
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    entered=threading.Event();reading=threading.Event();release=threading.Event();exited=threading.Event()
    @contextmanager
    def activation_view():
        entered.set()
        try:yield {"preview_sha256":hashlib.sha256(preview.read_bytes()).hexdigest()}
        finally:exited.set()
    original=module.authenticated_bounded_regular_read
    def blocked_read(path,limit,*,expected_sha256):
        if path==preview:
            assert entered.is_set() and not exited.is_set()
            reading.set();assert release.wait(5)
        return original(path,limit,expected_sha256=expected_sha256)
    monkeypatch.setattr(module,"authenticated_bounded_regular_read",blocked_read)
    with DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",
            activation_view=activation_view) as server:
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        response=[]
        client=threading.Thread(target=lambda:response.append(request(server,"/")),daemon=True);client.start()
        try:
            assert reading.wait(5) and not exited.is_set()
            release.set();client.join(timeout=5)
            assert response[0][0]==200 and response[0][2]==b"HOPE" and exited.wait(5)
        finally:
            release.set();server.shutdown();serving.join(timeout=5);client.join(timeout=5)


def test_activation_preview_rejects_same_size_content_substitution_with_restored_mtime(tmp_path):
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    identity=preview.stat();expected=hashlib.sha256(preview.read_bytes()).hexdigest()
    preview.write_text("NOPE")
    os.utime(preview,ns=(identity.st_atime_ns,identity.st_mtime_ns))
    @contextmanager
    def activation_view():
        yield {"preview_sha256":expected}
    with DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",activation_view=activation_view) as server:
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        try:
            status,_,body=request(server,"/")
            assert status==503 and body==b"Dashboard unavailable"
        finally:server.shutdown();serving.join(timeout=5)


def test_activation_preview_requires_independent_digest_before_local_read(tmp_path,monkeypatch):
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    reader=Mock()
    monkeypatch.setattr(module,"authenticated_bounded_regular_read",reader)
    @contextmanager
    def activation_view():
        yield {"preview_sha256":None}
    with DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",activation_view=activation_view) as server:
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        try:
            assert request(server,"/")[0]==503
            reader.assert_not_called()
        finally:server.shutdown();serving.join(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="Native socket backpressure is exercised on Linux CI")
def test_stalled_client_cannot_hold_activation_view_unbounded(tmp_path):
    preview=tmp_path/"preview.html";preview.write_bytes(b"x"*65536)
    entered=threading.Event();exited=threading.Event()
    @contextmanager
    def activation_view():
        entered.set()
        try:yield {"preview_sha256":hashlib.sha256(preview.read_bytes()).hexdigest()}
        finally:exited.set()
    with DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",
            activation_view=activation_view,client_io_timeout_seconds=0.2) as server:
        # Force response backpressure without relying on host socket defaults.
        server.socket.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024)
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        client=socket.socket()
        client.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
        try:
            client.connect(server.server_address)
            client.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            assert entered.wait(2)
            assert exited.wait(2),"stalled client retained activation lease"
            assert request(server,"/")[0]==200
        finally:
            client.close();server.shutdown();serving.join(timeout=5)


def test_concurrent_client_limit_rejects_excess_and_recovers_capacity(tmp_path):
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    with DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",
            client_io_timeout_seconds=2,max_concurrent_clients=2) as server:
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        clients=[]
        try:
            for _ in range(2):
                client=socket.create_connection(server.server_address,timeout=1)
                client.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n")
                clients.append(client)
            deadline=datetime.now(timezone.utc)+timedelta(seconds=1)
            while server._client_slots._value and datetime.now(timezone.utc)<deadline:
                threading.Event().wait(0.01)
            assert server._client_slots._value==0

            excess=socket.create_connection(server.server_address,timeout=1)
            try:
                excess.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                excess.settimeout(1)
                try:
                    assert excess.recv(1)==b""
                except ConnectionResetError:
                    pass
            finally:
                excess.close()

            clients.pop().close()
            deadline=datetime.now(timezone.utc)+timedelta(seconds=1)
            while server._client_slots._value==0 and datetime.now(timezone.utc)<deadline:
                threading.Event().wait(0.01)
            assert server.transport_status()=={
                "active_clients":1,"max_concurrent_clients":2,
                "rejected_clients_total":1,"expired_clients_total":0,
            }
            assert request(server,"/")[0]==200
        finally:
            for client in clients:client.close()
            server.shutdown();serving.join(timeout=5)


def test_total_client_deadline_rejects_slow_drip_and_recovers_capacity(tmp_path):
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    with DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",
            client_io_timeout_seconds=0.15,client_total_timeout_seconds=0.35,
            max_concurrent_clients=1) as server:
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        client=socket.create_connection(server.server_address,timeout=1)
        stop=threading.Event()
        def drip():
            while not stop.wait(0.03):
                try:client.sendall(b"G")
                except OSError:return
        dripping=threading.Thread(target=drip,daemon=True);dripping.start()
        try:
            deadline=time.monotonic()+1
            while server._client_slots._value and time.monotonic()<deadline:
                time.sleep(0.01)
            assert server._client_slots._value==0
            started=time.monotonic()
            while server._client_slots._value==0 and time.monotonic()-started<1:
                time.sleep(0.01)
            assert time.monotonic()-started<0.8
            assert server.transport_status()=={
                "active_clients":0,"max_concurrent_clients":1,
                "rejected_clients_total":0,"expired_clients_total":1,
            }
            assert request(server,"/")[0]==200
        finally:
            stop.set();client.close();dripping.join(timeout=1)
            server.shutdown();serving.join(timeout=5)


@pytest.mark.parametrize("value",[0,-1,True,61,"5"])
def test_invalid_client_io_timeout_is_rejected(tmp_path,value):
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    with pytest.raises(ValueError,match="CLIENT_TIMEOUT_INVALID"):
        DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",
            client_io_timeout_seconds=value)


@pytest.mark.parametrize("value",[0,-1,True,301,"15"])
def test_invalid_total_client_timeout_is_rejected(tmp_path,value):
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    with pytest.raises(ValueError,match="CLIENT_TOTAL_TIMEOUT_INVALID"):
        DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",
            client_total_timeout_seconds=value)


@pytest.mark.parametrize("value",[0,-1,True,257,1.5,"2"])
def test_invalid_concurrent_client_limit_is_rejected(tmp_path,value):
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    with pytest.raises(ValueError,match="CLIENT_LIMIT_INVALID"):
        DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",
            max_concurrent_clients=value)


@pytest.mark.parametrize("size,expected", [(40000, 200), (65537, 503)])
def test_m2_html_budget_is_independent_of_json_snapshot_budget(tmp_path, size, expected):
    with dashboard(tmp_path) as server:
        server.preview.write_bytes(b"x" * size)
        assert request(server, "/")[0] == expected


@pytest.mark.skipif(os.name != "posix", reason="Native inode types require POSIX")
@pytest.mark.parametrize("surface", ["preview", "snapshot", "legacy", "operations", "refresh"])
def test_server_refuses_symlinked_local_input_surfaces(tmp_path, surface):
    with dashboard(tmp_path, valid_snapshot()) as server:
        paths = {
            "preview": (server.preview, "/"),
            "snapshot": (server.snapshot, "/snapshot.json"),
            "legacy": (server.legacy, "/legacy-research.json"),
            "operations": (server.operations, "/operations.json"),
            "refresh": (server.refresh_status, "/refresh-status.json"),
        }
        path, endpoint = paths[surface]
        target = tmp_path / f"{surface}.target"
        if surface == "preview":
            target.write_text("<h1>substitute</h1>")
        elif surface == "snapshot":
            target.write_bytes(valid_snapshot())
        elif surface == "refresh":
            target.write_text(json.dumps(refresh_status()))
        else:
            target.write_text("{}")
        path.unlink(missing_ok=True)
        path.symlink_to(target)
        assert request(server, endpoint)[0] == 503


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


def classified_snapshot(*, generated_at=None, classification="DISPOSABLE_LAB",
                        database_identity="a"*64, synthetic_activity=True):
    data = json.loads(valid_snapshot(generated_at=generated_at))
    data["schema_version"] = 2
    data["deployed_commit"] = "a"*40
    data["provenance"] = {
        "data_classification": classification,
        "database_identity_sha256": database_identity,
        "synthetic_activity": synthetic_activity,
        "execution_authorized": False,
        "live_enabled": False,
    }
    return json.dumps(data).encode("utf-8")


def refresh_status(*, state="CURRENT", generated_at=None, commit="a"*40,
                   snapshot_generated_at=None, snapshot_sha256=None, error_code=None,
                   snapshot_data_classification=None,
                   snapshot_database_identity_sha256=None,
                   snapshot_synthetic_activity=None):
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    if state == "CURRENT" and snapshot_generated_at is None:
        snapshot_generated_at = generated_at
    if state == "CURRENT" and snapshot_sha256 is None:
        snapshot_sha256 = hashlib.sha256(
            classified_snapshot(generated_at=snapshot_generated_at)
        ).hexdigest()
    if state == "CURRENT":
        if snapshot_data_classification is None:
            snapshot_data_classification = "DISPOSABLE_LAB"
        if snapshot_database_identity_sha256 is None:
            snapshot_database_identity_sha256 = "a"*64
        if snapshot_synthetic_activity is None:
            snapshot_synthetic_activity = True
    return {
        "schema":"hope.dashboard-refresh.v3", "scope":"DISPOSABLE_LAB_ONLY",
        "generated_at":generated_at, "deployed_commit":commit,
        "paper_only":True, "live_enabled":False, "execution_authorized":False,
        "state":state, "snapshot_generated_at":snapshot_generated_at,
        "snapshot_sha256":snapshot_sha256,
        "snapshot_data_classification":snapshot_data_classification,
        "snapshot_database_identity_sha256":snapshot_database_identity_sha256,
        "snapshot_synthetic_activity":snapshot_synthetic_activity,
        "error_code":error_code,
    }


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
    (None, 200),
], ids=["malformed", "wrong-source", "oversized", "incomplete", "valid"])
def test_server_snapshot_schema_and_size_boundary(tmp_path, snapshot, expected):
    # A module-level valid_snapshot() is evaluated during collection.  The full
    # PostgreSQL matrix intentionally runs for longer than the freshness budget,
    # so create the positive fixture immediately before the request instead.
    if snapshot is None:
        snapshot = valid_snapshot()
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
            "snapshot_error_code":"SNAPSHOT_MISSING",
            "refresh_state":"UNAVAILABLE", "refresh_error_code":None,
        }


def test_server_reports_verified_refresh_success_without_database_details(tmp_path):
    stamp=datetime.now(timezone.utc).isoformat()
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(
        refresh_status(generated_at=stamp,snapshot_generated_at=stamp)))
    with dashboard(tmp_path,classified_snapshot(generated_at=stamp)) as server:
        status, headers, body = request(server, "/refresh-status.json")
        result = json.loads(body)
        assert status == 200 and headers["Cache-Control"].startswith("no-store")
        assert result["schema"] == "hope.dashboard-refresh-observation.v3"
        assert result["state"] == "CURRENT" and result["error_code"] is None
        assert result["execution_authorized"] is False and result["live_enabled"] is False
        assert set(result) == {"schema","scope","generated_at","deployed_commit",
                               "snapshot_generated_at","error_code","paper_only",
                               "snapshot_sha256","live_enabled","execution_authorized","state",
                               "snapshot_data_classification",
                               "snapshot_database_identity_sha256",
                               "snapshot_synthetic_activity"}
        assert result["snapshot_data_classification"] == "DISPOSABLE_LAB"
        assert result["snapshot_database_identity_sha256"] == "a"*64
        assert result["snapshot_synthetic_activity"] is True
        assert json.loads(request(server,"/status.json")[2])["refresh_state"]=="CURRENT"


def test_server_reports_closed_refresh_failure_while_last_good_is_preserved(tmp_path):
    value = refresh_status(state="FAILED", snapshot_generated_at=None,
                           error_code="DATABASE_IDENTITY_MISMATCH")
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(value))
    with dashboard(tmp_path, valid_snapshot()) as server:
        status, _, body = request(server, "/refresh-status.json")
        result = json.loads(body)
        assert status == 200 and result["state"] == "FAILED"
        assert result["snapshot_generated_at"] is None
        assert request(server, "/snapshot.json")[0] == 200
        health=json.loads(request(server,"/status.json")[2])
        assert health["refresh_state"]=="FAILED"
        assert health["refresh_error_code"]=="DATABASE_IDENTITY_MISMATCH"


@pytest.mark.parametrize("change", [
    {"error_code":"password=secret"}, {"execution_authorized":True},
    {"scope":"PRODUCTION"}, {"private_database":"hope"},
])
def test_server_rejects_unbounded_or_authorizing_refresh_status(tmp_path, change):
    value = refresh_status(state="FAILED", snapshot_generated_at=None,
                           error_code="SNAPSHOT_REFRESH_FAILED")
    value.update(change)
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(value))
    with dashboard(tmp_path) as server:
        status, _, body = request(server, "/refresh-status.json")
        assert status == 503 and body == b"Verified refresh status unavailable"


@pytest.mark.parametrize("digest", [None, "A"*64, "a"*63, 7])
def test_server_rejects_current_refresh_receipt_with_invalid_digest(tmp_path,digest):
    stamp=datetime.now(timezone.utc).isoformat()
    value=refresh_status(generated_at=stamp,snapshot_generated_at=stamp)
    value["snapshot_sha256"]=digest
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(value))
    with dashboard(tmp_path,classified_snapshot(generated_at=stamp)) as server:
        assert request(server,"/refresh-status.json")[0]==503


def test_server_marks_old_successful_refresh_attempt_stale(tmp_path):
    old = (datetime.now(timezone.utc)-timedelta(seconds=16)).isoformat()
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(
        refresh_status(generated_at=old, snapshot_generated_at=old)))
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    snapshot=tmp_path/"snapshot.json";snapshot.write_bytes(classified_snapshot(generated_at=old))
    with DashboardHTTPServer(0,preview,snapshot,max_snapshot_age_seconds=15) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            assert json.loads(request(server,"/refresh-status.json")[2])["state"]=="STALE"
        finally:server.shutdown();thread.join(timeout=5)


def test_failed_refresh_receipt_cannot_claim_snapshot_digest(tmp_path):
    value=refresh_status(
        state="FAILED", snapshot_generated_at=None,
        snapshot_sha256="b"*64, error_code="SNAPSHOT_REFRESH_FAILED",
    )
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(value))
    with dashboard(tmp_path,valid_snapshot()) as server:
        assert request(server,"/refresh-status.json")[0]==503


def test_server_rejects_current_refresh_receipt_for_different_snapshot(tmp_path):
    snapshot_stamp=datetime.now(timezone.utc).isoformat()
    receipt_stamp=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(
        refresh_status(generated_at=snapshot_stamp,snapshot_generated_at=receipt_stamp)))
    with dashboard(tmp_path,classified_snapshot(generated_at=snapshot_stamp)) as server:
        assert request(server,"/refresh-status.json")[0]==503
        health=json.loads(request(server,"/status.json")[2])
        assert health["data_feed"]=="CURRENT"
        assert health["refresh_state"]=="UNAVAILABLE"


def test_server_rejects_current_receipt_for_different_bytes_with_same_metadata(tmp_path):
    stamp=datetime.now(timezone.utc).isoformat()
    served=classified_snapshot(generated_at=stamp)
    different=json.loads(served)
    different["paper"]["job_runs"]["succeeded"] = 0
    different["paper"]["job_runs"]["failed"] = 2
    receipt=refresh_status(
        generated_at=stamp, snapshot_generated_at=stamp,
        snapshot_sha256=hashlib.sha256(json.dumps(different).encode()).hexdigest(),
    )
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(receipt))
    with dashboard(tmp_path,served) as server:
        assert request(server,"/snapshot.json")[0]==200
        assert request(server,"/refresh-status.json")[0]==503
        health=json.loads(request(server,"/status.json")[2])
        assert health["data_feed"]=="CURRENT"
        assert health["refresh_state"]=="UNAVAILABLE"


def test_server_rejects_lab_current_receipt_for_legacy_snapshot_downgrade(tmp_path):
    stamp = datetime.now(timezone.utc).isoformat()
    served = valid_snapshot(generated_at=stamp)
    receipt = refresh_status(
        generated_at=stamp, snapshot_generated_at=stamp,
        snapshot_sha256=hashlib.sha256(served).hexdigest(),
    )
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(receipt))
    with dashboard(tmp_path, served) as server:
        assert request(server, "/snapshot.json")[0] == 200
        assert request(server, "/refresh-status.json")[0] == 503
        assert json.loads(request(server, "/status.json")[2])["refresh_state"] == "UNAVAILABLE"


def test_server_rejects_lab_current_receipt_for_production_snapshot(tmp_path):
    stamp = datetime.now(timezone.utc).isoformat()
    served = classified_snapshot(
        generated_at=stamp, classification="PRODUCTION_READ_ONLY",
        synthetic_activity=False,
    )
    receipt = refresh_status(
        generated_at=stamp, snapshot_generated_at=stamp,
        snapshot_sha256=hashlib.sha256(served).hexdigest(),
    )
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(receipt))
    with dashboard(tmp_path, served) as server:
        assert request(server, "/snapshot.json")[0] == 200
        assert request(server, "/refresh-status.json")[0] == 503


def test_server_rejects_current_receipt_with_forged_database_identity(tmp_path):
    stamp = datetime.now(timezone.utc).isoformat()
    served = classified_snapshot(generated_at=stamp)
    receipt = refresh_status(
        generated_at=stamp, snapshot_generated_at=stamp,
        snapshot_sha256=hashlib.sha256(served).hexdigest(),
        snapshot_database_identity_sha256="b"*64,
    )
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(receipt))
    with dashboard(tmp_path, served) as server:
        assert request(server, "/refresh-status.json")[0] == 503


@pytest.mark.parametrize("field,value", [
    ("snapshot_data_classification", "DISPOSABLE_LAB"),
    ("snapshot_database_identity_sha256", "a"*64),
    ("snapshot_synthetic_activity", True),
])
def test_failed_refresh_receipt_cannot_claim_snapshot_provenance(tmp_path, field, value):
    receipt = refresh_status(
        state="FAILED", snapshot_generated_at=None,
        error_code="SNAPSHOT_REFRESH_FAILED",
    )
    receipt[field] = value
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(receipt))
    with dashboard(tmp_path, classified_snapshot()) as server:
        assert request(server, "/refresh-status.json")[0] == 503


def test_server_rejects_refresh_status_from_another_deployed_commit(tmp_path):
    (tmp_path/"snapshot-refresh.json").write_text(json.dumps(
        refresh_status(commit="b"*40)))
    preview=tmp_path/"preview.html";preview.write_text("HOPE")
    with DashboardHTTPServer(0,preview,tmp_path/"snapshot.json",
                             deployed_commit="a"*40) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            assert request(server,"/refresh-status.json")[0]==503
        finally:server.shutdown();thread.join(timeout=5)


def test_server_reports_aggregate_transport_status_separately(tmp_path):
    with dashboard(tmp_path) as server:
        status,headers,body=request(server,"/transport.json")
        assert status==200 and headers["Cache-Control"].startswith("no-store")
        assert json.loads(body)=={
            "schema_version":1,"active_clients":1,"max_concurrent_clients":32,
            "rejected_clients_total":0,"expired_clients_total":0,
        }


def test_server_reports_current_snapshot_and_never_exposes_db_details(tmp_path):
    with dashboard(tmp_path, valid_snapshot()) as server:
        status, _, body = request(server, "/status.json")
        assert status == 200
        state = json.loads(body)
        assert state["service"] == "RUNNING"
        assert state["data_feed"] == "CURRENT"
        assert state["snapshot_generated_at"]
        assert set(state) == {"schema_version", "service", "data_feed", "snapshot_generated_at",
                              "snapshot_error_code", "refresh_state", "refresh_error_code"}
        assert state["snapshot_error_code"] is None


def test_server_marks_old_snapshot_stale(tmp_path):
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with dashboard(tmp_path, valid_snapshot(generated_at=timestamp)) as server:
        status, _, body = request(server, "/status.json")
        assert status == 200
        assert json.loads(body)["data_feed"] == "STALE"
        status, _, body = request(server, "/snapshot.json")
        assert status == 503 and body == b"Verified snapshot unavailable"


def test_server_uses_one_fail_closed_snapshot_freshness_budget(tmp_path):
    timestamp = (datetime.now(timezone.utc) - timedelta(seconds=16)).isoformat()
    preview = tmp_path / "preview.html"; preview.write_text("HOPE")
    snapshot = tmp_path / "snapshot.json"; snapshot.write_bytes(valid_snapshot(generated_at=timestamp))
    with DashboardHTTPServer(0, preview, snapshot, max_snapshot_age_seconds=15) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            assert json.loads(request(server, "/status.json")[2])["data_feed"] == "STALE"
            assert request(server, "/snapshot.json")[0] == 503
        finally:
            server.shutdown(); thread.join(timeout=5)


@pytest.mark.parametrize("value", [0, 86401, True, 1.5, "900"])
def test_server_rejects_invalid_snapshot_freshness_budget(tmp_path, value):
    with pytest.raises(ValueError, match="MAX_SNAPSHOT_AGE_INVALID"):
        DashboardHTTPServer(0, tmp_path/"preview", tmp_path/"snapshot", max_snapshot_age_seconds=value)


def test_server_sanitizes_explicit_disposable_lab_provenance(tmp_path):
    data = json.loads(valid_snapshot())
    data.update(schema_version=2, provenance={
        "data_classification":"DISPOSABLE_LAB", "database_identity_sha256":"a"*64,
        "synthetic_activity":True, "execution_authorized":False, "live_enabled":False,
        "database_name":"NEVER_EXPOSE",
    })
    with dashboard(tmp_path, json.dumps(data).encode()) as server:
        assert request(server, "/snapshot.json")[0] == 503
    data["provenance"].pop("database_name")
    with dashboard(tmp_path, json.dumps(data).encode()) as server:
        status,_,body=request(server,"/snapshot.json")
        assert status==200 and json.loads(body)["provenance"]["data_classification"]=="DISPOSABLE_LAB"


def test_managed_server_refuses_snapshot_from_another_release(tmp_path):
    expected = "a" * 40
    for actual in (None, "b" * 40):
        data = json.loads(valid_snapshot())
        if actual is not None:
            data["deployed_commit"] = actual
        with dashboard(
            tmp_path, json.dumps(data).encode("utf-8"), deployed_commit=expected,
        ) as server:
            status, _, body = request(server, "/snapshot.json")
            assert status == 503 and body == b"Verified snapshot unavailable"
            status, _, body = request(server, "/status.json")
            assert status == 200
            assert json.loads(body) == {
                "schema_version": 1, "service": "RUNNING",
                "data_feed": "UNAVAILABLE", "snapshot_generated_at": None,
                "snapshot_error_code":"SNAPSHOT_RELEASE_MISMATCH",
                "refresh_state":"UNAVAILABLE", "refresh_error_code":None,
            }


def test_status_reports_bounded_invalid_snapshot_reason_without_input_details(tmp_path):
    with dashboard(tmp_path, b'{"password":"never expose"}') as server:
        status = json.loads(request(server, "/status.json")[2])
        assert status["snapshot_error_code"] == "SNAPSHOT_INVALID"
        assert "password" not in json.dumps(status)


def test_status_reports_untrusted_snapshot_file_without_accepting_it(tmp_path):
    preview = tmp_path / "preview.html"; preview.write_text("HOPE")
    snapshot = tmp_path / "snapshot.json"; snapshot.write_bytes(valid_snapshot())
    snapshot.chmod(0o666)
    with DashboardHTTPServer(0, preview, snapshot) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            status = json.loads(request(server, "/status.json")[2])
            assert status["snapshot_error_code"] == "SNAPSHOT_FILE_UNTRUSTED"
            assert request(server, "/snapshot.json")[0] == 503
        finally:
            server.shutdown(); thread.join(timeout=5)


def test_managed_server_serves_only_snapshot_from_exact_release(tmp_path):
    expected = "a" * 40
    data = json.loads(valid_snapshot())
    data["deployed_commit"] = expected
    with dashboard(
        tmp_path, json.dumps(data).encode("utf-8"), deployed_commit=expected,
    ) as server:
        status, _, body = request(server, "/snapshot.json")
        assert status == 200 and json.loads(body)["deployed_commit"] == expected
        status, _, body = request(server, "/status.json")
        assert status == 200 and json.loads(body)["data_feed"] == "CURRENT"


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

@pytest.mark.parametrize('endpoint',["/image.json","/history.json","/deployment.json","/publisher.json","/controller.json","/staging.json"])
def test_image_and_history_diagnostics_missing_evidence_are_bounded_and_unverified(tmp_path,endpoint):
    with dashboard(tmp_path) as server:
        status,_,payload=request(server,endpoint)
        assert status==200
        data=json.loads(payload)
    allowed={'UNAVAILABLE'} if endpoint=='/controller.json' else {'UNVERIFIED','UNKNOWN','BLOCKED'}
    assert data['state'] in allowed and data['execution_authorized'] is False
    assert str(tmp_path) not in json.dumps(data) and len(json.dumps(data))<1024
