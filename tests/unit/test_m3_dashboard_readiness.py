from datetime import UTC,datetime,timedelta
import json
from threading import Thread
from urllib.request import ProxyHandler,build_opener
from urllib.error import HTTPError

import pytest

from test_dashboard_m1_operations_surface import health
from test_dashboard_server import valid_snapshot
from hope.api.dashboard_readiness import dashboard_readiness
from hope.api.dashboard_server import DashboardHTTPServer,_verified_operations


@pytest.mark.parametrize("case,expected,reason",[
    ("missing","UNAVAILABLE","HEALTH_MISSING"),("invalid","UNAVAILABLE","HEALTH_INVALID"),
    ("unreadable","UNAVAILABLE","HEALTH_UNREADABLE"),("stale","UNAVAILABLE","HEALTH_STALE"),
    ("stopped","UNAVAILABLE","WORKER_STOPPED"),("halted","HALTED","DURABLE_HALT_OBSERVED"),
    ("snapshot_halt","HALTED","DURABLE_HALT_OBSERVED"),("missing_snapshot","UNAVAILABLE","SNAPSHOT_NOT_CURRENT"),
    ("identity","UNAVAILABLE","WORKER_IDENTITY_UNVERIFIED"),("current","READY","CURRENT_OBSERVATIONS_ONLY")])
def test_observations_distinguish_unavailable_causes_without_authorization(tmp_path,monkeypatch,case,expected,reason):
    snapshot=tmp_path/"snapshot.json";operations=tmp_path/"health.json"
    data=json.loads(valid_snapshot());data["paper"]["environment_state"]="HALTED" if case=="snapshot_halt" else "RUNNING"
    if case!="missing_snapshot":snapshot.write_text(json.dumps(data))
    value=health()
    if case=="stale":
        value["generated_at"]=value["last_success_at"]=(datetime.now(UTC)-timedelta(minutes=6)).isoformat()
    if case=="stopped":value["state"]="STOPPED"
    if case=="halted":value["state"]="HALTED"
    if case=="identity":value["deployed_commit"]=None
    if case!="missing":operations.write_text("broken{" if case=="invalid" else json.dumps(value))
    if case=="unreadable":
        monkeypatch.setattr("hope.api.dashboard_readiness._verified_operations",lambda *a:(_ for _ in ()).throw(PermissionError("private path secret")))
    result=dashboard_readiness(snapshot,operations)
    assert result["paper_health"]==expected and result["reason"]==reason
    assert result["execution_authorized"] is False and result["live_enabled"] is False
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("snapshot_commit", [None, "b" * 40])
@pytest.mark.parametrize("environment_state", ["RUNNING", "HALTED"])
def test_managed_readiness_rejects_fresh_snapshot_from_other_release(
    tmp_path, snapshot_commit, environment_state
):
    expected = "a" * 40
    snapshot, operations = tmp_path / "snapshot.json", tmp_path / "health.json"
    data = json.loads(valid_snapshot())
    data["paper"]["environment_state"] = environment_state
    data["deployed_commit"] = snapshot_commit
    snapshot.write_text(json.dumps(data))
    operations.write_text(json.dumps(health()))

    result = dashboard_readiness(snapshot, operations, expected_commit=expected)

    assert result["paper_health"] == "UNAVAILABLE"
    assert result["reason"] == "SNAPSHOT_IDENTITY_UNVERIFIED"
    assert result["database"] == "UNVERIFIED"
    assert result["execution_authorized"] is False


def test_managed_readiness_accepts_exact_snapshot_release(tmp_path):
    expected = "a" * 40
    snapshot, operations = tmp_path / "snapshot.json", tmp_path / "health.json"
    data = json.loads(valid_snapshot())
    data["paper"]["environment_state"] = "RUNNING"
    data["deployed_commit"] = expected
    snapshot.write_text(json.dumps(data))
    operations.write_text(json.dumps(health()))
    assert dashboard_readiness(
        snapshot, operations, expected_commit=expected
    )["paper_health"] == "READY"


def test_managed_readiness_rejects_fresh_heartbeat_from_other_paper_release(tmp_path):
    dashboard = "a" * 40
    paper = "b" * 40
    snapshot, operations = tmp_path / "snapshot.json", tmp_path / "health.json"
    data = json.loads(valid_snapshot())
    data["paper"]["environment_state"] = "RUNNING"
    data["deployed_commit"] = dashboard
    snapshot.write_text(json.dumps(data))
    operations.write_text(json.dumps({**health(), "deployed_commit": "c" * 40}))

    result = dashboard_readiness(
        snapshot, operations, expected_commit=dashboard, expected_paper_commit=paper,
    )

    assert result["paper_health"] == "UNAVAILABLE"
    assert result["reason"] == "WORKER_IDENTITY_UNVERIFIED"
    assert result["execution_authorized"] is False


def test_failure_history_is_bounded_and_retained_after_later_success(tmp_path,monkeypatch):
    from hope.infrastructure import paper_service as service
    from threading import Event
    stop=Event();calls=[]
    def cycle(*a,**k):
        calls.append(True)
        if len(calls)==1:raise RuntimeError("PAPER_MARKET_DATA_STALE")
        stop.set();return 0,0
    monkeypatch.setattr(service,"run_service_cycle",cycle)
    path=tmp_path/"health.json"
    service.run_service(object(),object(),migrations_dir=tmp_path,status_file=path,stop_event=stop,poll_seconds=1)
    safe=json.loads(_verified_operations(path))
    assert safe["state"]=="STOPPED" and safe["cycles_failed"]==safe["cycles_succeeded"]==1
    assert safe["last_failure_code"]=="PAPER_MARKET_DATA_STALE" and safe["last_failure_at"] is not None
    assert safe["last_success_at"]>=safe["last_failure_at"]


@pytest.mark.parametrize("change",[{"last_failure_at":"invalid","last_failure_code":"secret"},
    {"last_failure_at":None,"last_failure_code":"PAPER_SERVICE_CYCLE_FAILED"},
    {"last_failure_at":(datetime.now(UTC)+timedelta(days=1)).isoformat(),"last_failure_code":"secret"}])
def test_invalid_failure_history_cannot_be_exposed(tmp_path,change):
    path=tmp_path/"health.json";path.write_text(json.dumps({**health(),**change}))
    with pytest.raises(ValueError):_verified_operations(path)


def test_real_http_diagnosis_is_separate_from_operations_availability(tmp_path):
    page=tmp_path/"preview.html";page.write_text("HOPE")
    opener=build_opener(ProxyHandler({}))
    with DashboardHTTPServer(0,page,tmp_path/"snapshot.json",tmp_path/"health.json") as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with opener.open(base+"/readiness.json") as response:
                result=json.loads(response.read())
                assert response.status==200 and result["server"]=="RUNNING"
                assert result["paper_health"]=="UNAVAILABLE" and result["reason"]=="HEALTH_MISSING"
            with pytest.raises(HTTPError) as error:opener.open(base+"/operations.json")
            assert error.value.code==503;error.value.close()
            with opener.open(base+"/status.json") as response:
                assert json.loads(response.read())["service"]=="RUNNING"
        finally:server.shutdown();thread.join(timeout=5)
