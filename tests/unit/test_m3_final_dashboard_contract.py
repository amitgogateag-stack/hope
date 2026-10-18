"""Final deployment regressions; preparation never constitutes AWS activation."""
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path, PurePosixPath
import shlex
import os
import subprocess
import sys

import pytest

from hope.api import dashboard_activation as activation
from hope.api import operations_deployment as operations
from hope.api.dashboard_readiness import dashboard_readiness
from hope.api.m3_audit import _readiness
from test_dashboard_m1_operations_surface import health
from test_dashboard_server import valid_snapshot
from test_dashboard_only_activation import setup, activate, OLD, NEW, THIRD
from test_activation_guard import guard_platform


ROOT = Path(__file__).parents[2]


def test_privileged_release_inspection_does_not_execute_ambient_git(monkeypatch, tmp_path):
    calls=[]
    monkeypatch.setenv("PATH","/hostile")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL","/hostile/config")
    monkeypatch.setenv("LD_PRELOAD","/hostile/code")
    from types import SimpleNamespace
    monkeypatch.setattr(operations.subprocess,"run",lambda *a,**k:
        calls.append((a,k)) or SimpleNamespace(stdout="ok\n"))
    assert operations._git(tmp_path,"status","--porcelain")=="ok"
    command,options=calls[0][0][0],calls[0][1]
    assert command[0]=="/usr/bin/git" and "--no-optional-locks" in command
    assert "core.fsmonitor=false" in command and "core.hooksPath=/dev/null" in command
    assert options["env"]==operations.GIT_ENVIRONMENT
    assert not {"LD_PRELOAD","GIT_CONFIG_GLOBAL"} & set(options["env"])


@pytest.mark.parametrize("version,accepted", [(160013,True),(170009,True),(150000,False),(180000,False)])
def test_database_major_is_observed_read_only_and_unknown_versions_block(setup,monkeypatch,version,accepted):
    from unittest.mock import MagicMock,Mock
    layout,_=setup;layout.environment.write_text("HOPE_DATABASE_URL=isolated-test-placeholder\n")
    monkeypatch.setattr(activation,"_trusted",lambda *a,**k:None)
    engine=MagicMock();connection=engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.side_effect=["hope_m3_restore_test",str(version)]
    monkeypatch.setattr(activation,"create_engine",lambda *a,**k:engine)
    role,schema=Mock(),Mock()
    monkeypatch.setattr(activation.operations,"verify_database_role",role)
    monkeypatch.setattr(activation,"verify_schema_compatibility",schema)
    host=activation.Host(layout)
    if accepted:
        host.database(layout.root);assert host.postgres_version_num==version
        role.assert_called_once();schema.assert_called_once()
    else:
        with pytest.raises(RuntimeError,match="DATABASE_VERSION_UNVERIFIED"):host.database(layout.root)
        role.assert_not_called();schema.assert_not_called()
    engine.dispose.assert_called_once()


def test_managed_commands_restore_reviewed_path_after_systemd_unset():
    layout = activation.Layout(root=PurePosixPath("/opt/hope-operations"))
    for unit, override in activation._configs(layout, "a" * 40).items():
        base = (ROOT / "deploy/operations" / unit).read_text()
        assert "PYTHONPATH" in next(line for line in base.splitlines() if line.startswith("UnsetEnvironment="))
        commands = [line.split("=", 1)[1] for line in override.splitlines()
                    if line.startswith(("ExecStart=", "ExecStartPre=")) and line.split("=",1)[1]]
        assert len(commands) == 2
        for command in commands:
            assert shlex.split(command)[:3] == ["/usr/bin/env",
                "PYTHONPATH=/opt/hope-operations/dashboard-current/src", str(layout.python)]
            assert "-B -s -P" in command
        assert "hope-paper.service" not in override


@pytest.mark.parametrize("case", ["future_health", "old_poll", "future_snapshot", "zero_cycles", "current_error"])
def test_future_or_inconsistent_observations_cannot_report_ready(tmp_path, case):
    snapshot, operations = tmp_path / "snapshot", tmp_path / "health"
    value, data = health(), json.loads(valid_snapshot())
    data["paper"]["environment_state"] = "RUNNING"
    if case == "future_health":
        value["generated_at"] = value["last_success_at"] = (datetime.now(UTC)+timedelta(seconds=60)).isoformat()
    elif case == "old_poll":
        value["last_success_at"] = (datetime.now(UTC)-timedelta(minutes=10)).isoformat()
    elif case == "future_snapshot":
        data["generated_at"] = (datetime.now(UTC)+timedelta(seconds=60)).isoformat()
    elif case == "zero_cycles":
        value["cycles_succeeded"] = 0
    else:
        value["error_code"] = "PAPER_SERVICE_DATABASE_UNAVAILABLE"
    snapshot.write_text(json.dumps(data)); operations.write_text(json.dumps(value))
    result = dashboard_readiness(snapshot, operations)
    assert result["paper_health"] == "UNAVAILABLE"
    assert result["execution_authorized"] is False and result["live_enabled"] is False
    assert _readiness(result)["paper_health"] == "UNAVAILABLE"


@pytest.mark.parametrize("change", ["missing", "old", "future"])
def test_audit_rejects_ready_without_a_current_successful_poll(tmp_path, change):
    snapshot, operations = tmp_path/"snapshot", tmp_path/"health"
    data=json.loads(valid_snapshot());data["paper"]["environment_state"]="RUNNING"
    snapshot.write_text(json.dumps(data));operations.write_text(json.dumps(health()))
    result=dashboard_readiness(snapshot,operations)
    assert result["paper_health"]=="READY"
    if change=="missing":result["last_successful_poll_at"]=None
    elif change=="old":result["last_successful_poll_at"]=(datetime.now(UTC)-timedelta(minutes=10)).isoformat()
    else:
        result["generated_at"]=result["last_successful_poll_at"]=(datetime.now(UTC)+timedelta(seconds=60)).isoformat()
    with pytest.raises(ValueError):_readiness(result)


@pytest.mark.parametrize("unavailable", ["missing", "invalid", "stale", "future"])
def test_current_snapshot_halt_survives_unavailable_health(tmp_path, unavailable):
    snapshot,operations=tmp_path/"snapshot",tmp_path/"health"
    data=json.loads(valid_snapshot());data["paper"]["environment_state"]="HALTED"
    snapshot.write_text(json.dumps(data));value=health()
    if unavailable=="invalid":operations.write_text("invalid{")
    elif unavailable!="missing":
        value["generated_at"]=value["last_success_at"]=(datetime.now(UTC)+
            (timedelta(seconds=60) if unavailable=="future" else -timedelta(minutes=6))).isoformat()
        operations.write_text(json.dumps(value))
    result=dashboard_readiness(snapshot,operations)
    assert result["paper_health"]=="HALTED" and result["reason"]=="DURABLE_HALT_OBSERVED"
    assert _readiness(result)["paper_health"]=="HALTED"


def test_stale_snapshot_halt_is_not_a_current_halt_observation(tmp_path):
    snapshot,operations=tmp_path/"snapshot",tmp_path/"health"
    data=json.loads(valid_snapshot());data["paper"]["environment_state"]="HALTED"
    data["generated_at"]=(datetime.now(UTC)-timedelta(hours=2)).isoformat()
    snapshot.write_text(json.dumps(data));operations.write_text(json.dumps(health()))
    result=dashboard_readiness(snapshot,operations)
    assert result["paper_health"]=="UNAVAILABLE" and result["reason"]=="SNAPSHOT_NOT_CURRENT"
    assert result["database"]=="UNVERIFIED" and result["reconciliation"]=="UNVERIFIED"


def test_upgrade_and_rollback_preserve_exact_reviewed_legacy_dropins(setup):
    layout,host=setup
    activate(layout,host)
    legacy=activation._legacy_configs(layout,NEW)
    for unit,payload in legacy.items():activation._write(activation._config_path(layout,unit),payload.encode())
    assert activation._check_configs(layout,NEW)==legacy
    activate(layout,host,target=THIRD)
    assert activation._backup(layout,activation._record(layout))["configs"]==legacy
    activation.rollback(layout,host,target=THIRD,shared=OLD)
    assert activation._check_configs(layout,NEW)==legacy
    assert activation._pointer(layout,"current")==OLD
    assert activation._record(layout)["target"]==NEW


def test_mixed_legacy_and_new_dropins_are_not_adopted(setup):
    layout,host=setup;activate(layout,host)
    activation._write(activation._config_path(layout,activation.WEB),
        activation._legacy_configs(layout,NEW)[activation.WEB].encode())
    host.calls.clear()
    with pytest.raises(RuntimeError,match="OVERRIDE_CHANGED"):activate(layout,host,target=THIRD)
    assert host.calls==[] and activation._pointer(layout,"current")==OLD


@pytest.mark.skipif(os.name!="posix",reason="Native systemd exec environment; exercised on Linux CI")
def test_real_python_launch_after_base_unit_environment_scrub(tmp_path):
    layout=activation.Layout(root=tmp_path)
    layout.python.parent.mkdir(parents=True);os.symlink(sys.executable,layout.python)
    for unit,payload in activation._configs(layout,NEW).items():
        base=(ROOT/"deploy/operations"/unit).read_text()
        environment={"PATH":"/usr/bin:/bin","HOME":"/nonexistent",
            "PYTHONPATH":"/hostile","PYTHONHOME":"/hostile"}
        for line in (base+"\n"+payload).splitlines():
            if line.startswith("UnsetEnvironment="):
                for key in line.split("=",1)[1].split():environment.pop(key,None)
        for line in payload.splitlines():
            if not line.startswith(("ExecStart=","ExecStartPre=")) or line.endswith("="):continue
            arguments=shlex.split(line.split("=",1)[1]);arguments=arguments[:arguments.index("-m")]
            probe="import os,sys,json;print(json.dumps([os.environ.get('PYTHONPATH'),sys.flags.safe_path,sys.flags.no_user_site,sys.dont_write_bytecode]))"
            result=subprocess.run([*arguments,"-c",probe],check=True,capture_output=True,text=True,
                timeout=10,env=environment)
            assert json.loads(result.stdout)==[str(tmp_path/"dashboard-current/src"),True,1,True]
