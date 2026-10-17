from unittest.mock import Mock
from urllib.error import HTTPError
from datetime import UTC,datetime,timedelta
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
from urllib.request import ProxyHandler,build_opener

import pytest

from hope.api import m3_audit as module
from hope.api.dashboard_readiness import dashboard_readiness
from hope.api.dashboard_server import DashboardHTTPServer
from test_dashboard_m1_operations_surface import health
from test_dashboard_server import valid_snapshot


def response_for(monkeypatch,body):
    response=Mock();response.status=200;response.read.return_value=body
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    opener=Mock();opener.open.return_value=response
    monkeypatch.setattr(module,"build_opener",lambda *a:opener)
    return response


def readiness(tmp_path):
    return dashboard_readiness(tmp_path/"absent-snapshot",tmp_path/"absent-health")


def test_systemd_output_is_whitelisted_and_never_runs_mutations(monkeypatch):
    runner=Mock(return_value=Mock(stdout="ActiveState=inactive\nSubState=dead\nUnitFileState=disabled\nEnvironment=secret"))
    monkeypatch.setattr(module.subprocess,"run",runner)
    result=module._unit("hope-paper.service")
    assert result=={"ActiveState":"inactive","SubState":"dead","UnitFileState":"disabled"}
    assert runner.call_args.args[0][:2]==["systemctl","show"]
    assert "secret" not in str(result)


def test_systemd_permission_failure_is_unavailable_not_raw_error(monkeypatch):
    monkeypatch.setattr(module.subprocess,"run",Mock(side_effect=PermissionError("secret")))
    assert module._unit("hope-paper.service")=={"status":"UNAVAILABLE"}


def test_http_503_remains_explicit_without_echoing_body(monkeypatch):
    opener=Mock();opener.open.side_effect=HTTPError("private",503,"secret",{},None)
    monkeypatch.setattr(module,"build_opener",lambda *a:opener)
    assert module._endpoint("/operations.json")=={"http_status":503,"status":"UNAVAILABLE"}


def test_http_success_filters_raw_metadata(monkeypatch):
    response=Mock();response.status=200
    response.read.return_value=b'{"service":"RUNNING","data_feed":"UNAVAILABLE","password":"secret","state":"secret"}'
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    opener=Mock();opener.open.return_value=response
    monkeypatch.setattr(module,"build_opener",lambda *a:opener)
    assert module._endpoint("/status.json")=={"http_status":200,"service":"RUNNING","data_feed":"UNAVAILABLE"}
    assert opener.open.call_args.args[0]=="http://127.0.0.1:8788/status.json"


def test_readiness_failure_details_survive_audit_without_raw_metadata(tmp_path,monkeypatch):
    value=readiness(tmp_path)
    value.update(last_failure_at=value["generated_at"],last_failure_code="database password secret",
        password="secret",connection_string="secret",worker_pid="secret")
    response=response_for(monkeypatch,json.dumps(value).encode())
    result=module._endpoint("/readiness.json")
    assert result["reason"]=="HEALTH_MISSING" and result["server"]=="RUNNING"
    assert result["worker_report"]=="UNKNOWN" and result["database"]=="UNVERIFIED"
    assert result["last_failure_at"]==value["generated_at"]
    assert result["last_failure_code"]=="PAPER_SERVICE_CYCLE_FAILED"
    assert result["execution_authorized"] is False and result["live_enabled"] is False
    assert "secret" not in json.dumps(result) and "worker_pid" not in result
    response.read.assert_called_once_with(32769)


@pytest.mark.parametrize("change",[
    {"schema":"secret"},{"paper_only":1},{"live_enabled":0},{"execution_authorized":True},
    {"reason":"secret"},{"reason":[]},{"worker_report":"secret"},{"snapshot":"secret"},
    {"database":"secret"},{"reconciliation":"secret"},{"server":"secret"},
    {"paper_health":"READY"},{"generated_at":None},{"generated_at":"secret"},
    {"generated_at":"2026-10-17T12:00:00"},{"last_successful_poll_at":"secret"},
    {"last_successful_paper_at":"secret"},{"last_failure_code":"secret"},
    {"last_failure_at":"2026-10-17T12:00:00+00:00"},
])
def test_invalid_readiness_is_http_200_invalid_response_not_ready(tmp_path,monkeypatch,change):
    response_for(monkeypatch,json.dumps({**readiness(tmp_path),**change}).encode())
    result=module._endpoint("/readiness.json")
    assert result=={"http_status":200,"status":"INVALID_RESPONSE"}
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("case",["stale","future","poll_after_generation","failure_after_generation"])
def test_readiness_clock_checks_cannot_replay_ready(tmp_path,monkeypatch,case):
    value=readiness(tmp_path)
    if case in {"stale","future"}:
        value["generated_at"]=(datetime.now(UTC)+timedelta(minutes=-6 if case=="stale" else 6)).isoformat()
    else:
        key="last_successful_poll_at" if case=="poll_after_generation" else "last_failure_at"
        value[key]=(datetime.now(UTC)+timedelta(seconds=1)).isoformat()
        if key=="last_failure_at":value["last_failure_code"]="PAPER_SERVICE_CYCLE_FAILED"
    response_for(monkeypatch,json.dumps(value).encode())
    assert module._endpoint("/readiness.json")=={"http_status":200,
        "status":"STALE_RESPONSE" if case=="stale" else "INVALID_RESPONSE"}


@pytest.mark.parametrize("body",[b"secret",b"[]",b"null",b"\xff",b"x"*32769],
    ids=["malformed","array","null","non-utf8","oversize"])
def test_malformed_or_oversize_http_body_is_bounded_and_sanitized(monkeypatch,body):
    response_for(monkeypatch,body)
    assert module._endpoint("/readiness.json")=={"http_status":200,"status":"INVALID_RESPONSE"}


def test_unknown_endpoint_never_opens_a_connection(monkeypatch):
    opener=Mock();monkeypatch.setattr(module,"build_opener",opener)
    assert module._endpoint("/private?secret")=={"http_status":None,"status":"INVALID_ENDPOINT"}
    opener.assert_not_called()


@pytest.mark.parametrize("change",[
    {"reason":"HEALTH_STALE"},{"worker_report":"STOPPED"},{"snapshot":"STALE"},
    {"database":"UNVERIFIED"},{"reconciliation":"CLAIMS_REQUIRE_REVIEW"},
])
def test_ready_cannot_override_conflicting_observations(tmp_path,monkeypatch,change):
    value={**readiness(tmp_path),"paper_health":"READY","reason":"CURRENT_OBSERVATIONS_ONLY",
        "worker_report":"CURRENT","snapshot":"CURRENT","database":"OBSERVED_AT_SNAPSHOT",**change}
    response_for(monkeypatch,json.dumps(value).encode())
    assert module._endpoint("/readiness.json")=={"http_status":200,"status":"INVALID_RESPONSE"}


def test_old_ready_response_cannot_be_replayed_as_current(tmp_path,monkeypatch):
    value={**readiness(tmp_path),"paper_health":"READY","reason":"CURRENT_OBSERVATIONS_ONLY",
        "worker_report":"CURRENT","snapshot":"CURRENT","database":"OBSERVED_AT_SNAPSHOT",
        "generated_at":(datetime.now(UTC)-timedelta(minutes=6)).isoformat()}
    response_for(monkeypatch,json.dumps(value).encode())
    assert module._endpoint("/readiness.json")=={"http_status":200,"status":"STALE_RESPONSE"}


def test_real_http_redirect_cannot_escape_the_fixed_endpoint(monkeypatch):
    reached=[]
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append(self.path)
            self.send_response(302)
            self.send_header("Location","/private?secret")
            self.end_headers()
        def log_message(self,*args):pass
    opener=build_opener(ProxyHandler({}),module._NoRedirect())
    with ThreadingHTTPServer(("127.0.0.1",0),Redirect) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        adapter=Mock();adapter.open.side_effect=lambda url,**kw:opener.open(
            url.replace(":8788",f":{server.server_port}"),**kw)
        monkeypatch.setattr(module,"build_opener",lambda *a:adapter)
        try:
            assert module._endpoint("/readiness.json")=={"http_status":302,"status":"UNAVAILABLE"}
            assert reached==["/readiness.json"]
        finally:server.shutdown();thread.join(timeout=5)


@pytest.mark.parametrize("case,expected,reason,operations_status",[
    ("missing","UNAVAILABLE","HEALTH_MISSING",503),
    ("invalid","UNAVAILABLE","HEALTH_INVALID",503),
    ("stale","UNAVAILABLE","HEALTH_STALE",200),
    ("stopped","UNAVAILABLE","WORKER_STOPPED",200),
    ("halted","HALTED","DURABLE_HALT_OBSERVED",200),
    ("current","READY","CURRENT_OBSERVATIONS_ONLY",200),
])
def test_audit_consumes_actual_dashboard_http_without_conflating_liveness(
        tmp_path,monkeypatch,case,expected,reason,operations_status):
    page=tmp_path/"preview.html";page.write_text("HOPE")
    snapshot=tmp_path/"snapshot.json";data=json.loads(valid_snapshot())
    data["paper"]["environment_state"]="RUNNING";snapshot.write_text(json.dumps(data))
    operations=tmp_path/"health.json";value=health()
    if case=="stale":
        value["generated_at"]=value["last_success_at"]=(datetime.now(UTC)-timedelta(minutes=6)).isoformat()
    if case=="stopped":value["state"]="STOPPED"
    if case=="halted":value["state"]="HALTED"
    if case!="missing":operations.write_text("broken{" if case=="invalid" else json.dumps(value))
    opener=build_opener(ProxyHandler({}),module._NoRedirect())
    with DashboardHTTPServer(0,page,snapshot,operations) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        local=f"http://127.0.0.1:{server.server_port}"
        # Only the test adapter changes the port; production remains fixed loopback 8788.
        adapter=Mock();adapter.open.side_effect=lambda url,**kw:opener.open(
            url.replace("http://127.0.0.1:8788",local),**kw)
        monkeypatch.setattr(module,"build_opener",lambda *a:adapter)
        try:
            result=module._endpoint("/readiness.json")
            assert result["http_status"]==200 and result["server"]=="RUNNING"
            assert result["paper_health"]==expected and result["reason"]==reason
            assert result["execution_authorized"] is False
            assert module._endpoint("/operations.json")["http_status"]==operations_status
        finally:server.shutdown();thread.join(timeout=5)
