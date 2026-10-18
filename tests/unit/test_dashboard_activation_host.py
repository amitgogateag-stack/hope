from datetime import UTC,datetime,timedelta
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,MagicMock
from urllib.error import HTTPError

import pytest

from hope.api import dashboard_activation as module
from hope.api.dashboard_readiness import dashboard_readiness
from hope.api.dashboard_server import DashboardHTTPServer
from test_dashboard_only_activation import OLD,NEW,setup
from test_activation_guard import guard_platform
from test_dashboard_server import valid_snapshot


@pytest.fixture
def preflight(setup,monkeypatch):
    layout,_=setup;layout.snapshot.write_bytes(valid_snapshot())
    host=module.Host(layout)
    host.paper_safe=Mock();host.release=Mock(side_effect=lambda sha:layout.root/"releases"/sha)
    path=layout.root/"releases"/NEW/"src/hope/api";path.mkdir(parents=True)
    (path/"dashboard_activation.py").write_text("reviewed code")
    host.runtime=Mock();host.database=Mock();host.configuration=Mock();host.listener=Mock()
    host.show=Mock(return_value={"ActiveState":"inactive"})
    monkeypatch.setattr(module,"verify_exact_ci",Mock(return_value={"sha":NEW}))
    monkeypatch.setattr(module.shutil,"disk_usage",lambda *a:SimpleNamespace(free=256*1024*1024))
    original=Path.read_text
    monkeypatch.setattr(Path,"read_text",lambda path,*a,**k:
        "MemAvailable: 262144 kB\n" if path.as_posix()=="/proc/meminfo" else original(path,*a,**k))
    return layout,host


def test_complete_preflight_checks_both_schema_versions_before_mutation(preflight):
    layout,host=preflight
    assert host.preflight(NEW,OLD,None,123,1) is False
    assert [call.args[0].name for call in host.database.call_args_list]==[NEW,OLD]
    assert host.runtime.call_args.kwargs=={"new":True}
    module.verify_exact_ci.assert_called_once_with(NEW,123,1)


@pytest.mark.parametrize("failure",["ci","dirty","dependency","database","configuration","listener","snapshot_missing","snapshot_invalid","snapshot_stale","disk","memory"])
def test_adversarial_preflight_checks_block_without_service_controls(preflight,monkeypatch,failure):
    layout,host=preflight;host.control=Mock()
    method={"ci":None,"dirty":"release","dependency":"runtime","database":"database",
        "configuration":"configuration","listener":"listener"}.get(failure)
    if failure=="ci":monkeypatch.setattr(module,"verify_exact_ci",Mock(side_effect=RuntimeError("wrong marker")))
    elif method:setattr(host,method,Mock(side_effect=RuntimeError("private secret")))
    elif failure=="snapshot_missing":layout.snapshot.unlink()
    elif failure=="snapshot_invalid":layout.snapshot.write_text("secret")
    elif failure=="snapshot_stale":layout.snapshot.write_bytes(valid_snapshot(generated_at=(datetime.now(UTC)-timedelta(hours=2)).isoformat()))
    elif failure=="disk":monkeypatch.setattr(module.shutil,"disk_usage",lambda *a:SimpleNamespace(free=1))
    elif failure=="memory":
        original=Path.read_text
        monkeypatch.setattr(Path,"read_text",lambda p,*a,**k:"MemAvailable: 1 kB\n" if p.as_posix()=="/proc/meminfo" else original(p,*a,**k))
    with pytest.raises((OSError,ValueError,RuntimeError)):host.preflight(NEW,OLD,None,123,1)
    host.control.assert_not_called()


def test_database_uses_read_only_transaction_and_rejects_wrong_target(setup,monkeypatch):
    layout,_=setup;layout.environment.write_text("HOPE_DATABASE_URL=postgresql+psycopg://private\n")
    monkeypatch.setattr(module,"_trusted",lambda *a,**k:None)
    engine=MagicMock();connection=engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value="wrong_database"
    monkeypatch.setattr(module,"create_engine",Mock(return_value=engine))
    with pytest.raises(RuntimeError,match="WRONG_DATABASE"):module.Host(layout).database(layout.root)
    assert str(connection.execute.call_args_list[0].args[0])=="SET TRANSACTION READ ONLY"
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("failure",[None,"role","schema"])
def test_database_role_and_schema_are_checked_without_sql_mutations(setup,monkeypatch,failure):
    layout,_=setup;layout.environment.write_text("HOPE_DATABASE_URL='postgresql+psycopg://private'\n")
    monkeypatch.setattr(module,"_trusted",lambda *a,**k:None)
    engine=MagicMock();connection=engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value="hope_m3_restore_test"
    monkeypatch.setattr(module,"create_engine",Mock(return_value=engine))
    role=Mock(side_effect=RuntimeError("role secret") if failure=="role" else None)
    schema=Mock(side_effect=RuntimeError("schema secret") if failure=="schema" else None)
    monkeypatch.setattr(module.operations,"verify_database_role",role)
    monkeypatch.setattr(module,"verify_schema_compatibility",schema)
    if failure:
        with pytest.raises(RuntimeError):module.Host(layout).database(layout.root)
    else:module.Host(layout).database(layout.root)
    role.assert_called_once_with(connection,component="dashboard")
    assert all(str(c.args[0]).startswith(("SET TRANSACTION READ ONLY","SELECT current_database")) for c in connection.execute.call_args_list)
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("content",["secret","HOPE_DATABASE_URL=x\nHOPE_DATABASE_URL=y\n",
    "HOPE_LIVE_ENABLED=true\nHOPE_DATABASE_URL=x\n","PYTHONPATH=/old/release\nHOPE_DATABASE_URL=x\n",
    "LD_PRELOAD=unreviewed\nHOPE_DATABASE_URL=x\n","HOPE_EXECUTION_MODE=LIVE\nHOPE_DATABASE_URL=x\n"])
def test_unsafe_environment_file_is_never_executed_or_used_to_connect(setup,monkeypatch,content):
    layout,_=setup;layout.environment.write_text(content)
    monkeypatch.setattr(module,"_trusted",lambda *a,**k:None)
    engine=Mock();monkeypatch.setattr(module,"create_engine",engine)
    with pytest.raises(RuntimeError):module.Host(layout).database(layout.root)
    engine.assert_not_called()


def test_host_stop_and_start_preserve_timer_enablement_and_never_touch_paper():
    host=module.Host(module.Layout());host.run=Mock(return_value="")
    host.legacy_safe=Mock()
    host.show=Mock(side_effect=lambda unit:{"ActiveState":"inactive","UnitFileState":"disabled"})
    host.stop();host.reload();host.start(True)
    commands=[c.args for c in host.run.call_args_list]
    assert commands==[("systemctl","stop",module.TIMER,module.EXPORT,module.WEB),
        ("systemctl","daemon-reload"),("systemctl","reset-failed",*module.DASHBOARD_UNITS),
        ("systemctl","start",module.WEB),("systemctl","start",module.TIMER)]


def test_http_verification_accepts_missing_paper_health_without_inventing_ready(setup,monkeypatch):
    layout,_=setup;host=module.Host(layout);host.paper_safe=Mock();host.listener=Mock()
    page=layout.root/"releases"/NEW/"docs/dashboard/preview.html"
    page.parent.mkdir(parents=True);page.write_bytes(b"HOPE")
    layout.snapshot.write_bytes(valid_snapshot())
    ready=dashboard_readiness(layout.snapshot,layout.health)
    identity=dict(schema="hope.dashboard-release.v1",dashboard_commit=NEW,
        paper_only=True,live_enabled=False,execution_authorized=False)
    replies={"/":(200,b"HOPE"),"/status.json":(200,b'{"service":"RUNNING"}'),
        "/snapshot.json":(200,module._verified_snapshot(layout.snapshot)[0]),"/operations.json":(503,b''),
        "/release.json":(200,json.dumps(identity).encode()),"/readiness.json":(200,json.dumps(ready).encode())}
    host.request=Mock(side_effect=lambda p:replies[p]);host.verify(NEW,managed=True)
    replies["/snapshot.json"]=(503,b"")
    host.verify(NEW,managed=True,require_fresh=False)
    replies["/snapshot.json"]=(200,module._verified_snapshot(layout.snapshot)[0])
    replies["/release.json"]=(200,b'{"dashboard_commit":"secret"}')
    monkeypatch.setattr(module.time,"sleep",lambda *a:None)
    with pytest.raises(RuntimeError,match="VERIFICATION_TIMEOUT"):host.verify(NEW,managed=True)


@pytest.mark.parametrize("body",[b"body",b"x"*(2*1024*1024+1)],ids=["bounded","oversize"])
def test_fixed_http_transport_bounds_response_and_refuses_unknown_path(monkeypatch,body):
    response=Mock();response.status=200;response.read.return_value=body
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    opener=Mock();opener.open.return_value=response
    monkeypatch.setattr(module,"build_opener",lambda *a:opener)
    host=module.Host(module.Layout())
    if len(body)>2*1024*1024:
        with pytest.raises(RuntimeError,match="BODY_TOO_LARGE"):host.request("/")
    else:assert host.request("/")==(200,body)
    response.read.assert_called_once_with(2*1024*1024+1)
    with pytest.raises(RuntimeError,match="HTTP_PATH_DENIED"):host.request("http://private")
    opener.open.side_effect=HTTPError("private",503,"secret",{},None)
    assert host.request("/operations.json")== (503,b"")


def test_release_identity_endpoint_is_unavailable_until_pinned_and_is_read_only(tmp_path):
    from threading import Thread
    from urllib.request import ProxyHandler,build_opener,Request
    page=tmp_path/"page";page.write_text("HOPE")
    opener=build_opener(ProxyHandler({}))
    for commit in (None,NEW):
        with DashboardHTTPServer(0,page,tmp_path/"snapshot",deployed_commit=commit) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f"http://127.0.0.1:{server.server_port}"
            try:
                if commit is None:
                    with pytest.raises(HTTPError) as error:opener.open(base+"/release.json")
                    assert error.value.code==503;error.value.close()
                else:
                    with opener.open(base+"/release.json") as response:
                        assert json.loads(response.read())["dashboard_commit"]==NEW
                    with pytest.raises(HTTPError) as error:opener.open(Request(base+"/release.json",method="POST"))
                    assert error.value.code==405;error.value.close()
            finally:server.shutdown();thread.join(timeout=5)
    with pytest.raises(ValueError,match="COMMIT_INVALID"):
        DashboardHTTPServer(0,page,tmp_path/"snapshot",deployed_commit="secret")


@pytest.mark.parametrize("failure",[None,"public","occupied","wrong_pid","wrong_python"])
def test_private_listener_must_belong_to_exact_systemd_python(setup,monkeypatch,failure):
    layout,_=setup;host=module.Host(layout)
    layout.python.parent.mkdir(parents=True);layout.python.write_text("python")
    host.show=Mock(return_value={"MainPID":"321"})
    line='LISTEN 0 128 127.0.0.1:8788 0.0.0.0:* users:(("python",pid=321,fd=3))'
    if failure=="public":line=line.replace("127.0.0.1:8788","0.0.0.0:8788")
    if failure=="occupied":line+="\n"+line
    if failure=="wrong_pid":line=line.replace("pid=321,","pid=999,")
    host.run=Mock(return_value=line)
    original=Path.resolve
    monkeypatch.setattr(Path,"resolve",lambda p,*a,**k:
        layout.root/"wrong-python" if p.as_posix()=="/proc/321/exe" and failure=="wrong_python" else
        original(layout.python,*a,**k) if p.as_posix()=="/proc/321/exe" else original(p,*a,**k))
    if failure:
        with pytest.raises(RuntimeError):host.listener()
    else:host.listener()


@pytest.mark.parametrize("change",[None,"base_unit","dropin","disabled_web","export_busy"])
def test_configuration_compatibility_refuses_unknown_dropins_and_mutation(setup,monkeypatch,change):
    layout,_=setup;host=module.Host(layout)
    monkeypatch.setattr(module,"_trusted",lambda *a,**k:None)
    baseline=layout.root/"releases"/OLD/"deploy/operations";baseline.mkdir(parents=True)
    for unit in module.DASHBOARD_UNITS:
        (baseline/unit).write_text("reviewed unit")
        (layout.units/unit).write_text("reviewed unit")
    def show(unit):
        return {"FragmentPath":str(layout.units/unit),"DropInPaths":"secret" if change=="dropin" else "",
            "ActiveState":"active" if unit==module.WEB or (unit==module.EXPORT and change=="export_busy") else "inactive",
            "UnitFileState":"disabled" if change=="disabled_web" else "enabled"}
    host.show=Mock(side_effect=show)
    if change=="base_unit":(layout.units/module.WEB).write_text("unreviewed unit")
    if change:
        with pytest.raises(RuntimeError):host.configuration(OLD,None)
    else:host.configuration(OLD,None)


def test_release_trust_and_git_inspection_precede_imports(setup,monkeypatch):
    layout,_=setup;host=module.Host(layout);release=layout.root/"releases"/NEW
    (release/".git/objects/info").mkdir(parents=True)
    monkeypatch.setattr(module,"_trusted",Mock())
    inspect=Mock();monkeypatch.setattr(module.operations,"inspect_release",inspect)
    assert host.release(NEW)==release
    inspect.assert_called_once_with(release,NEW)
    (release/".git/objects/info/alternates").write_text("outside")
    with pytest.raises(RuntimeError,match="EXTERNAL_GIT_STATE"):host.release(NEW)


def test_release_scan_is_bounded_before_git_or_import_execution(setup,monkeypatch):
    layout,_=setup;release=layout.root/"releases"/NEW
    (release/".git").mkdir()
    monkeypatch.setattr(module,"_trusted",lambda *a,**k:None)
    monkeypatch.setattr(module.os,"walk",lambda *a,**k:[(str(release),["too-many"]*50001,[])])
    inspect=Mock();monkeypatch.setattr(module.operations,"inspect_release",inspect)
    with pytest.raises(RuntimeError,match="RELEASE_SCAN_LIMIT"):module.Host(layout).release(NEW)
    inspect.assert_not_called()


@pytest.mark.parametrize("action",["preflight","activate","rollback","gate","status"])
def test_cli_dispatches_fixed_layout_and_keeps_preflight_read_only(setup,monkeypatch,capsys,action):
    from test_dashboard_only_activation import activate
    layout,host=setup
    if action in {"rollback","gate"}:activate(layout,host)
    host.calls.clear()
    monkeypatch.setattr(module,"Layout",lambda:layout)
    monkeypatch.setattr(module,"Host",lambda l:host)
    monkeypatch.setattr(module.sys,"platform","linux")
    result=module.main([action,"--target",NEW,"--expected-current",OLD,"--ci-run-id","123",
        "--ci-run-attempt","1","--approve-dashboard-change"])
    assert result==0 and "secret" not in capsys.readouterr().out
    if action=="preflight":assert host.calls==["preflight"]


def test_live_http_fence_closes_after_owner_death_then_committed_boot_is_readable(setup,monkeypatch):
    from threading import Thread
    from urllib.request import ProxyHandler,build_opener
    from test_dashboard_only_activation import activate
    layout,host=setup;activate(layout,host)
    record=module._record(layout);record["phase"]="VERIFYING";module._save(layout.journal,record)
    page=layout.root/"page";page.write_text("HOPE")
    opener=build_opener(ProxyHandler({}))
    with DashboardHTTPServer(0,page,layout.snapshot,deployed_commit=NEW,
            activation_check=lambda:module.runtime_gate(layout,NEW)) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with opener.open(base+"/status.json") as response:assert response.status==200
            monkeypatch.setattr(module,"_owner_alive",lambda *a:False)
            for path in ("/","/status.json","/release.json","/readiness.json"):
                with pytest.raises(HTTPError) as error:opener.open(base+path)
                assert error.value.code==503 and b"secret" not in error.value.read();error.value.close()
            record["phase"]="COMMITTED";module._save(layout.journal,record)
            with opener.open(base+"/release.json") as response:
                assert json.loads(response.read())["dashboard_commit"]==NEW
        finally:server.shutdown();thread.join(timeout=5)


def test_source_identity_rejects_a_falsely_declared_launch_sha(monkeypatch,tmp_path):
    monkeypatch.setenv("PYTHONPATH",str(tmp_path/"dashboard-current/src"))
    with pytest.raises(RuntimeError,match="CODE_IDENTITY_MISMATCH"):
        module._code_identity(module.Layout(root=tmp_path),NEW)


@pytest.mark.parametrize("fields,accepted",[
    ("LoadState=not-found\n",True),
    ("LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\n",True),
    ("LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n",False),
    ("LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\n",False),
    ("secret",False)])
def test_legacy_unit_cannot_compete_with_canonical_dashboard(fields,accepted):
    host=module.Host(module.Layout());host.run=Mock(return_value=fields)
    if accepted:host.legacy_safe()
    else:
        with pytest.raises(RuntimeError,match="LEGACY_DASHBOARD_MUST"):host.legacy_safe()


def test_missing_legacy_unit_nonzero_requires_explicit_not_found_evidence():
    host=module.Host(module.Layout())
    host.run=Mock(side_effect=subprocess.CalledProcessError(4,"systemctl",output="LoadState=not-found\n"))
    host.legacy_safe()
    host.run=Mock(side_effect=subprocess.CalledProcessError(1,"systemctl",output="private error"))
    with pytest.raises(RuntimeError,match="LEGACY_DASHBOARD_UNVERIFIED"):host.legacy_safe()
