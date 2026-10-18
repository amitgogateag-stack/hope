"""Real durable files and guarded orchestration; host/service probes are injected."""
from contextlib import contextmanager
from datetime import UTC,datetime,timedelta
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hope.api import dashboard_activation as module
from hope.api import operations_deployment as operations
from test_activation_guard import guard_platform
from test_dashboard_server import valid_snapshot

OLD="1"*40
NEW="2"*40
THIRD="3"*40


class FakeHost:
    def __init__(self):self.calls=[];self.failure=None;self.timer=False
    def step(self,name):
        self.calls.append(name)
        if self.failure==name:
            self.failure=None
            raise RuntimeError("injected private secret")
    def paper_safe(self):self.step("paper_safe")
    def preflight(self,*args):self.step("preflight");return self.timer
    def rollback_preflight(self,*args):self.step("rollback_preflight")
    def stop(self):self.step("stop")
    def reload(self):self.step("reload")
    def start(self,timer):self.step("start");assert timer is self.timer
    def verify(self,*args,**kwargs):self.step("verify")


@pytest.fixture
def setup(tmp_path_factory,monkeypatch,guard_platform):
    # Keep native Windows paths below MAX_PATH even for realistic unit names.
    tmp_path=tmp_path_factory.mktemp("da")
    layout=module.Layout(root=tmp_path,units=tmp_path/"units",snapshot=tmp_path/"snapshot.json",
        health=tmp_path/"health.json",environment=tmp_path/"reader.env")
    layout.units.mkdir()
    for sha in (OLD,NEW,THIRD):(tmp_path/"releases"/sha).mkdir(parents=True)
    monkeypatch.setattr(module,"OWNER",os.getuid() if hasattr(os,"getuid") else 0)
    # Real source-path binding is exercised by the native separate web process;
    # these filesystem orchestration fixtures do not copy an entire release.
    monkeypatch.setattr(module,"_code_identity",lambda *a:None)
    if os.name=="nt":
        # Windows cannot grant POSIX symlinks/fsync/ownership. Model pointer
        # metadata locally; Linux CI runs these exact operations natively.
        monkeypatch.setattr(module,"_sync",lambda *a:None)
        monkeypatch.setattr(module,"_trusted",lambda *a,**k:None)
        monkeypatch.setattr(module.os,"fchmod",lambda *a:None,raising=False)
        def pointer(layout,name,optional=False):
            path=layout.root/name
            if optional and not path.exists():return None
            return path.read_text()
        def point(layout,sha):
            path=layout.root/"dashboard-current"
            if sha is None:path.unlink()
            else:path.write_text(sha)
        monkeypatch.setattr(module,"_pointer",pointer);monkeypatch.setattr(module,"_point",point)
        (tmp_path/"current").write_text(OLD)
    else:os.symlink(tmp_path/"releases"/OLD,tmp_path/"current",target_is_directory=True)
    return layout,FakeHost()


def activate(layout,host,target=NEW):
    return module.activate(layout,host,target=target,shared=OLD,ci_run=123,ci_attempt=1)


@pytest.mark.parametrize("timer",[False,True])
def test_activation_preserves_shared_pointer_and_boot_gate_then_rolls_back(setup,timer):
    layout,host=setup;host.timer=timer
    result=activate(layout,host)
    assert result["dashboard_commit"]==NEW and result["paper_activated"] is False
    assert module._pointer(layout,"current")==OLD
    assert module._pointer(layout,"dashboard-current")==NEW
    assert module._record(layout)["phase"]=="COMMITTED"
    assert module.gate(layout,NEW,host)["status"]=="GATE_PASSED"
    for content in module._configs(layout,NEW).values():
        assert "dashboard-current" in content and "ExecStartPre=" in content
        assert "hope-paper.service" not in content
    result=module.rollback(layout,host,target=NEW,shared=OLD)
    assert result["dashboard_commit"]==OLD and module._record(layout) is None
    assert module._pointer(layout,"dashboard-current",optional=True) is None
    assert module._pointer(layout,"current")==OLD
    assert not module._config_path(layout,module.WEB).exists()
    assert list(layout.root.glob("dashboard-rollback-*.json"))


def test_second_dashboard_upgrade_can_restore_previous_committed_override(setup):
    layout,host=setup;activate(layout,host)
    first=layout.journal.read_bytes()
    activate(layout,host,THIRD)
    module.rollback(layout,host,target=THIRD,shared=OLD)
    assert layout.journal.read_bytes()==first
    assert module.gate(layout,NEW,host)["status"]=="GATE_PASSED"
    module.rollback(layout,host,target=NEW,shared=OLD)
    assert not layout.journal.exists()


@pytest.mark.parametrize("field,value",[
    ("pid","invalid"),("boot","invalid"),("ticks","invalid"),
    ("backup_id","invalid"),("phase","PREPARED"),("extra","unexpected")])
def test_nested_rollback_rejects_malformed_prior_journal_before_host_mutation(setup,field,value):
    layout,host=setup;activate(layout,host);activate(layout,host,THIRD)
    record=module._record(layout)
    backup=layout.root/("dashboard-backup-"+record["backup_id"]+".json")
    payload=json.loads(backup.read_text());payload["previous_record"][field]=value
    module._save(backup,payload,0o600);host.calls.clear()
    with pytest.raises(RuntimeError,match="BACKUP_INVALID"):
        module.rollback(layout,host,target=THIRD,shared=OLD)
    assert host.calls==["paper_safe"]
    assert module._pointer(layout,"dashboard-current")==THIRD
    assert module._record(layout)==record


@pytest.mark.parametrize("failure",["preflight","stop","reload","start","verify","paper_safe"])
def test_failures_never_switch_shared_release_and_restore_original(setup,failure):
    layout,host=setup;host.failure=failure
    with pytest.raises(RuntimeError):activate(layout,host)
    assert module._pointer(layout,"current")==OLD
    assert module._pointer(layout,"dashboard-current",optional=True) is None
    assert module._record(layout) is None


@pytest.mark.parametrize("point",["configuration","pointer","journal"])
def test_partial_mutation_or_keyboard_interrupt_rolls_back(setup,monkeypatch,point):
    layout,host=setup
    name={"configuration":"_install_configs","pointer":"_point","journal":"_save"}[point]
    original=getattr(module,name);failed=False
    def interrupt(*args,**kwargs):
        nonlocal failed
        result=original(*args,**kwargs)
        relevant=point!="journal" or (args[0]==layout.journal and args[1]["phase"]=="VERIFYING")
        if relevant and not failed:failed=True;raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(module,name,interrupt)
    with pytest.raises(KeyboardInterrupt):activate(layout,host)
    assert module._record(layout) is None
    assert module._pointer(layout,"dashboard-current",optional=True) is None


def test_failed_rollback_is_durable_and_blocks_boot_until_explicit_recovery(setup,monkeypatch):
    layout,host=setup
    host.verify=Mock(side_effect=RuntimeError("secret"))
    with pytest.raises(RuntimeError):activate(layout,host)
    assert module._record(layout)["phase"]=="ROLLBACK_FAILED"
    with pytest.raises(RuntimeError,match="RECOVERY_REQUIRED"):module.gate(layout,NEW,host)
    with pytest.raises(RuntimeError,match="RECOVERY_REQUIRED"):activate(layout,host)
    host.verify=Mock();monkeypatch.setattr(module,"_owner_alive",lambda *a:False)
    module.rollback(layout,host,target=NEW,shared=OLD)
    assert module._record(layout) is None and module._pointer(layout,"current")==OLD


def test_pending_owner_blocks_recovery_but_dead_owner_can_restore(setup,monkeypatch):
    layout,host=setup;activate(layout,host)
    record=module._record(layout);record["phase"]="VERIFYING";module._save(layout.journal,record)
    with pytest.raises(RuntimeError,match="OWNER_STILL_ACTIVE"):
        module.rollback(layout,host,target=NEW,shared=OLD)
    monkeypatch.setattr(module,"_owner_alive",lambda *a:False)
    with pytest.raises(RuntimeError,match="RECOVERY_REQUIRED"):module.gate(layout,NEW,host)
    module.rollback(layout,host,target=NEW,shared=OLD)


def test_failed_activation_quiesces_even_when_rollback_preflight_is_impossible(setup):
    layout,host=setup
    host.verify=Mock(side_effect=RuntimeError("startup secret"))
    host.rollback_preflight=Mock(side_effect=RuntimeError("prior database unavailable"))
    with pytest.raises(RuntimeError):activate(layout,host)
    assert module._record(layout)["phase"]=="ROLLBACK_FAILED"
    assert host.calls[-1]=="stop" and host.calls.count("stop")>=2


@pytest.mark.parametrize("change",["backup","override","shared","target","full_lock"])
def test_changed_state_is_not_overwritten_or_started(setup,monkeypatch,change):
    layout,host=setup;activate(layout,host);host.calls.clear()
    if change=="backup":
        record=module._record(layout)
        (layout.root/("dashboard-backup-"+record["backup_id"]+".json")).write_text('{}')
    if change=="override":module._config_path(layout,module.WEB).write_text("secret")
    if change=="shared":
        original=module._pointer
        monkeypatch.setattr(module,"_pointer",lambda l,n,**k:THIRD if n=="current" else original(l,n,**k))
    if change=="full_lock":(layout.root/".activation.lock").write_text("pending")
    with pytest.raises((RuntimeError,KeyError)):
        if change=="override":module.gate(layout,NEW,host)
        else:module.rollback(layout,host,target=THIRD if change=="target" else NEW,shared=OLD)
    assert "start" not in host.calls


def test_concurrent_guard_blocks_dashboard_and_full_release_before_callbacks(setup):
    layout,host=setup
    with operations._activation_guard(layout.root):
        with pytest.raises(operations.DeploymentError,match="LOCKED"):activate(layout,host)
    assert not host.calls
    activate(layout,host)
    with pytest.raises(operations.DeploymentError,match="DASHBOARD_OVERRIDE"):
        operations._switch_release(layout.root,THIRD,expected_current=OLD,
            accept=lambda:pytest.fail("accepted"),before_switch=lambda:pytest.fail("stopped PAPER"))


def test_legacy_recovery_and_canonical_activation_share_one_guard(setup):
    from hope.api import dashboard_deployment as legacy
    layout,host=setup
    with operations._activation_guard(layout.root):
        with pytest.raises(operations.DeploymentError,match="LOCKED"):
            with legacy._guard(layout.root):pytest.fail("legacy raced canonical operation")
    activate(layout,host)
    with pytest.raises(operations.DeploymentError,match="JOURNAL_WORKFLOW_REQUIRED"):
        with legacy._guard(layout.root):pytest.fail("legacy bypassed canonical journal")


@pytest.mark.parametrize("name",[".dashboard-activation.lock",".dashboard-current.next","dashboard-deployed.env"])
def test_unmigrated_legacy_state_blocks_before_any_host_calls(setup,name):
    layout,host=setup;(layout.root/name).write_text("legacy state")
    with pytest.raises(RuntimeError,match="LEGACY_DASHBOARD_REQUIRES_REVIEW"):activate(layout,host)
    assert host.calls==[]


@pytest.mark.parametrize("target",[None,"secret","7e9d341742818a97accce3e2301b1c7f96596795"])
def test_invalid_or_frozen_release_never_calls_host(setup,target):
    layout,host=setup
    with pytest.raises(RuntimeError):activate(layout,host,target)
    assert not host.calls


def test_service_mutation_firewall_and_private_commands():
    host=module.Host(module.Layout());host.run=Mock(return_value="")
    for verb,units in [("start",(module.PAPER,)),("enable",(module.WEB,)),("restart",(module.PAPER,)),("stop",())]:
        with pytest.raises(RuntimeError,match="MUTATION_DENIED"):host.control(verb,*units)
    host.run.assert_not_called()
    host.control("stop",*module.DASHBOARD_UNITS)
    assert module.PAPER not in host.run.call_args.args
    with pytest.raises(RuntimeError,match="UNIT_DENIED"):host.show("secret")


@pytest.mark.parametrize("state,enabled",[("active","disabled"),("inactive","enabled"),("failed","disabled"),("inactive","unknown")])
def test_paper_activity_or_boot_enablement_blocks_every_dashboard_start(state,enabled):
    host=module.Host(module.Layout());host.show=Mock(return_value={"ActiveState":state,"UnitFileState":enabled})
    host.control=Mock()
    with pytest.raises(RuntimeError,match="PAPER_MUST"):host.start(False)
    host.control.assert_not_called()


@pytest.mark.parametrize("change",[{"st_uid":999999},{"st_mode":0o100622},{"st_mode":0o120777},{"st_nlink":2}])
def test_trust_check_rejects_mutable_wrong_owner_link_and_hardlink(monkeypatch,change):
    info=dict(st_mode=0o100600,st_uid=module.OWNER,st_nlink=1);info.update(change)
    path=Mock();path.lstat.return_value=SimpleNamespace(**info)
    with pytest.raises(RuntimeError,match="UNTRUSTED_PATH"):module._trusted(path)


def test_host_runtime_preflight_is_scoped_and_dependency_importing(setup,monkeypatch):
    layout,_=setup;host=module.Host(layout)
    (layout.root/"venv/bin").mkdir(parents=True);layout.python.write_text("python")
    monkeypatch.setattr(module,"_trusted",lambda *a,**k:None)
    host.run=Mock(return_value="DASHBOARD_RUNTIME_VERIFIED\n")
    host.runtime(layout.root/"releases"/NEW,new=True)
    args=host.run.call_args.args
    assert args[:4]==("runuser","--user","hope-dashboard","--")
    assert "sqlalchemy,psycopg" in args[-1] and "(3,13)" in args[-1]
    assert "dashboard_activation" in args[-1] and "_verified_snapshot" in args[-1]
    assert Path(host.run.call_args.kwargs["env"]["PYTHONPATH"])==layout.root/"releases"/NEW/"src"
    host.run=Mock(return_value="Python secret wrong version")
    with pytest.raises(RuntimeError,match="RUNTIME_UNVERIFIED"):host.runtime(layout.root/"releases"/NEW)


@pytest.mark.parametrize("action",["activate","rollback"])
def test_cli_requires_approval_before_any_platform_or_host_probe(monkeypatch,capsys,action):
    monkeypatch.setattr(module,"Host",Mock(side_effect=lambda l:Mock()))
    with pytest.raises(SystemExit) as error:module.main([action,"--target",NEW,"--expected-current",OLD])
    assert error.value.code==2 and "secret" not in capsys.readouterr().err


def test_owner_unknown_is_not_treated_as_dead(monkeypatch):
    record={"pid":5,"boot":"1"*36,"ticks":"10"}
    monkeypatch.setattr(operations,"_process_identity",Mock(side_effect=PermissionError("private")))
    with pytest.raises(PermissionError):module._owner_alive(record)
    monkeypatch.setattr(operations,"_process_identity",Mock(side_effect=FileNotFoundError()))
    assert not module._owner_alive(record)
