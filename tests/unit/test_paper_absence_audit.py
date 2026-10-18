import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from hope.api import dashboard_activation as activation
from hope.api import paper_absence_audit as module
from test_dashboard_only_activation import setup, OLD, NEW, activate
from test_activation_guard import guard_platform

ABSENT = dict(LoadState="not-found", ActiveState="inactive", UnitFileState="",
              MainPID="0", FragmentPath="", DropInPaths="")
INSTALLED = dict(LoadState="loaded", ActiveState="inactive", UnitFileState="disabled",
    MainPID="0", FragmentPath="/etc/systemd/system/hope-paper.service", DropInPaths="")


def properties(fields):
    return "".join(f"{key}={value}\n" for key, value in fields.items())


@pytest.mark.parametrize("changed", [{}, {"ActiveState":"active"},
    {"ActiveState":"activating"}, {"ActiveState":"failed"}, {"MainPID":"44"},
    {"UnitFileState":"disabled"}, {"FragmentPath":"/tmp/alternative"},
    {"DropInPaths":"/run/systemd/system/hope-paper.service.d/override.conf"},
    {"LoadState":"error"}, {"ActiveState":"unknown"}])
def test_absence_is_never_an_activation_permit(changed):
    host = activation.Host(activation.Layout())
    host.run = Mock(return_value=properties(ABSENT | changed))
    host.legacy_safe = Mock()
    with pytest.raises(RuntimeError):
        host.paper_safe()
    host.legacy_safe.assert_not_called()
    assert all(call.args[1] == "show" for call in host.run.call_args_list)


@pytest.mark.parametrize("raw", ["", "LoadState=not-found\n", properties(ABSENT)+"LoadState=not-found\n",
    properties(ABSENT)+"Unknown=secret\n", properties(ABSENT).replace("MainPID=0\n", ""),
    "secret"*20000, None], ids=["empty","partial","duplicate","unknown","missing","oversize","none"])
def test_incomplete_ambiguous_and_oversize_properties_are_unverified(raw):
    assert module.unit_state(raw) == "UNVERIFIED"


@pytest.mark.parametrize("code,raw,usable", [(4,properties(ABSENT),True),
    (1,properties(ABSENT),True),(2,properties(ABSENT),False),
    (4,"LoadState=not-found\n",False),(1,"secret",False)])
def test_nonzero_systemctl_requires_complete_absent_record(code,raw,usable):
    host = activation.Host(activation.Layout())
    host.run = Mock(side_effect=subprocess.CalledProcessError(code,"systemctl",output=raw))
    if usable:
        assert host.paper_properties() == raw
        with pytest.raises(RuntimeError,match="ABSENCE_REQUIRES_HOST_REVIEW"):
            host.paper_safe()
    else:
        with pytest.raises(RuntimeError,match="UNIT_UNVERIFIED"):
            host.paper_properties()


@pytest.mark.parametrize("change,accepted", [({},True),({"UnitFileState":"masked"},True),
    ({"LoadState":"masked","UnitFileState":"masked","FragmentPath":"/dev/null"},True),
    ({"LoadState":"masked"},False),
    ({"ActiveState":"activating"},False),({"LoadState":"not-found"},False),
    ({"UnitFileState":"enabled"},False),({"MainPID":"1"},False),
    ({"DropInPaths":"/tmp/override"},False),({"FragmentPath":"relative"},False)])
def test_installed_guard_rejects_nonzero_pid_unknown_load_and_overrides(change,accepted):
    host = activation.Host(activation.Layout())
    host.run = Mock(return_value=properties(INSTALLED | change));host.legacy_safe=Mock()
    if accepted:
        host.paper_safe();host.legacy_safe.assert_called_once()
    else:
        with pytest.raises(RuntimeError):host.paper_safe()


def process(proc, command=b"/usr/bin/python\0-m\0hope.infrastructure.paper_service\0", cgroup=b"0::/rogue.service"):
    entry=proc/"42";entry.mkdir(parents=True)
    (entry/"stat").write_bytes(b"42 (worker with spaces) S "+b"0 "*18+b"123 0\n")
    (entry/"cmdline").write_bytes(command);(entry/"cgroup").write_bytes(cgroup)
    return entry


@pytest.mark.parametrize("command,cgroup", [
    (b"python\0-m\0hope.infrastructure.paper_service\0",b"0::/other.service"),
    (b"/opt/hope-operations/venv/bin/python\0runner.py",b"0::/unrelated.service"),
    (b"/usr/local/bin/renamed\0",b"0::/hope-other.service"),
    (b"python\0paper_worker.py\0secret-token",b"0::/session.scope")])
def test_rogue_processes_are_reported_without_exporting_arguments(tmp_path,command,cgroup):
    process(tmp_path,command,cgroup)
    assert module.process_inventory(tmp_path)==(1,1)


@pytest.mark.parametrize("failure", ["missing", "oversize", "empty", "reuse", "denied"])
def test_incomplete_process_visibility_is_not_clean(tmp_path,monkeypatch,failure):
    entry=process(tmp_path)
    if failure=="missing":(entry/"cmdline").unlink()
    elif failure=="oversize":(entry/"cmdline").write_bytes(b"x"*(module.BYTE_LIMIT+1))
    elif failure=="empty":
        for path in entry.iterdir():path.unlink()
        entry.rmdir()
    elif failure=="reuse":
        original=module._read;reads=iter([b"42 (x) S "+b"0 "*18+b"123",b"42 (x) S "+b"0 "*18+b"124"])
        monkeypatch.setattr(module,"_read",lambda p:next(reads) if p.name=="stat" else original(p))
    else:monkeypatch.setattr(module,"_read",Mock(side_effect=PermissionError("secret")))
    with pytest.raises((ValueError,OSError)):module.process_inventory(tmp_path)


def systemd_run(name="unrelated.service", command="/usr/bin/python -m hope.infrastructure.paper_service", extra=()):
    def run(*args):
        if args[1] in {"list-units","list-unit-files"}:
            key="unit" if args[1]=="list-units" else "unit_file"
            return json.dumps([{key:value} for value in (name,*extra)])
        unit=args[2]
        return properties(dict(Id=unit,LoadState="loaded",ActiveState="inactive",
            ExecStart=command,FragmentPath="/etc/systemd/system/"+unit,DropInPaths=""))
    return run


@pytest.mark.parametrize("name", ["renamed.service","worker@tenant.service","unrelated.service"])
def test_alternative_service_names_are_inspected_by_execution_content(name):
    run=Mock(side_effect=systemd_run(name))
    assert module.systemd_inventory(run)==(1,1,0)
    assert run.call_args.args[2]==name


def test_inactive_timers_paths_and_sockets_require_review():
    assert module.systemd_inventory(systemd_run(command="/usr/bin/true",
        extra=("nightly.timer","hidden.path","activation.socket")))==(4,0,3)


@pytest.mark.parametrize("raw", ["secret", "[]", "{}", '[{"unit":"--host=remote"}]',
    '[{"unit":"worker.service"},{"unit":null}]'])
def test_unparseable_or_partial_systemd_inventory_is_unverified(raw):
    with pytest.raises((ValueError,TypeError)):module.systemd_inventory(lambda *a:raw)


def test_scheduler_jobs_and_user_units_are_counted_without_content(tmp_path):
    cron=tmp_path/"cron.d";cron.mkdir();(cron/"renamed").write_text("* * * * * user python runner.py secret")
    user=tmp_path/"user";user.mkdir();(user/"worker.service").write_text("ExecStart=/hidden/paper")
    assert module.schedule_inventory((cron,user,tmp_path/"absent"))==(4,2)


def test_scheduler_permission_failures_and_scan_limits_are_unverified(tmp_path,monkeypatch):
    job=tmp_path/"job";job.write_text("secret")
    monkeypatch.setattr(module,"_read",Mock(side_effect=PermissionError("secret")))
    with pytest.raises(PermissionError):module.schedule_inventory((job,))
    monkeypatch.setattr(module,"LIMIT",0)
    with pytest.raises(ValueError):module.schedule_inventory((tmp_path,))


@pytest.mark.parametrize("conflict", [".activation.lock",".dashboard-activation.lock",
    ".dashboard-current.next","dashboard-deployed.env","journal","pointer"])
def test_deployment_conflicts_never_produce_a_clean_audit(setup,conflict):
    layout,fake=setup
    if conflict in {"journal","pointer"}:
        activate(layout,fake)
        value=activation._record(layout)
        value["phase"]="PREPARED" if conflict=="journal" else "COMMITTED"
        if conflict=="pointer":value["shared"]=NEW
        activation._save(layout.journal,value)
    else:(layout.root/conflict).write_text("pending")
    with pytest.raises(RuntimeError):activation.Host(layout).absence_deployment_check()


def test_clean_pattern_scans_cannot_certify_renamed_workers_or_restart_safety(tmp_path):
    process(tmp_path,b"/usr/local/bin/innocent-name",b"0::/session.scope")
    host=Mock();host.paper_properties.return_value=properties(ABSENT)
    host.run.side_effect=systemd_run(command="/usr/bin/true")
    host.absence_deployment_check.return_value=(0,)
    result=module.audit(host,proc=tmp_path,schedules=())
    assert result["status"]=="BLOCKED" and result["activation_authorized"] is False
    assert result["execution_authorized"] is False
    assert result["checks"]["processes"]["counts"]==(1,0)
    assert all(x["status"]=="REVIEW_REQUIRED" for k,x in result["checks"].items() if k!="canonical_unit")
    assert "innocent-name" not in json.dumps(result)


def test_failed_diagnostics_are_redacted_and_do_not_skip_other_checks(tmp_path):
    host=Mock();host.paper_properties.side_effect=RuntimeError("secret")
    host.run.side_effect=RuntimeError("credential")
    host.absence_deployment_check.side_effect=RuntimeError("token")
    result=module.audit(host,proc=tmp_path,schedules=(tmp_path,))
    assert result["checks"]["canonical_unit"]=="UNVERIFIED"
    assert result["checks"]["processes"]["status"]=="UNVERIFIED"
    assert result["checks"]["systemd"]["status"]=="UNVERIFIED"
    assert result["checks"]["deployment"]["status"]=="UNVERIFIED"
    assert not any(secret in json.dumps(result) for secret in ("secret","credential","token"))
    host.absence_deployment_check.assert_called_once()


def test_user_schedule_locations_do_not_read_home_contents(tmp_path):
    home=tmp_path/"home";home.mkdir();user=home/"operator";user.mkdir()
    (user/"private-key").write_text("never read")
    paths=module.schedule_paths(home)
    assert user/".config/systemd/user" in paths
    assert user/".local/share/systemd/user" in paths
    assert all("private-key" not in str(path) for path in paths)


@pytest.mark.parametrize("kind", ["file","limit"])
def test_ambiguous_home_inventory_is_blocked(tmp_path,monkeypatch,kind):
    if kind=="file":(tmp_path/"not-a-home").write_text("secret")
    else:
        (tmp_path/"operator").mkdir();monkeypatch.setattr(module,"LIMIT",0)
    with pytest.raises(ValueError):module.schedule_paths(tmp_path)


@pytest.mark.parametrize("inventory", ["process","schedule","systemd"])
def test_inventories_have_a_wall_clock_bound(tmp_path,monkeypatch,inventory):
    process(tmp_path)
    ticks=iter([0,11]);monkeypatch.setattr(module.time,"monotonic",lambda:next(ticks))
    with pytest.raises(ValueError):
        if inventory=="process":module.process_inventory(tmp_path)
        elif inventory=="schedule":module.schedule_inventory((tmp_path,))
        else:module.systemd_inventory(systemd_run())


def test_cli_audit_is_read_only_and_returns_failure_even_for_complete_diagnostics(monkeypatch,capsys):
    from types import SimpleNamespace
    monkeypatch.setattr(activation.sys,"platform","linux")
    monkeypatch.setattr(activation,"os",SimpleNamespace(**vars(activation.os)))
    monkeypatch.setattr(activation.os,"geteuid",lambda:0,raising=False)
    host=Mock();monkeypatch.setattr(activation,"Host",lambda layout:host)
    result=dict(status="BLOCKED",execution_authorized=False,activation_authorized=False)
    monkeypatch.setattr(module,"audit",Mock(return_value=result))
    assert activation.main(["paper-audit"])==2
    assert json.loads(capsys.readouterr().out)==result
    assert host.mock_calls==[]


def test_audit_requires_root_without_attempting_any_inventory(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(activation.sys,"platform","linux")
    monkeypatch.setattr(activation,"os",SimpleNamespace(**vars(activation.os)))
    monkeypatch.setattr(activation.os,"geteuid",lambda:1001,raising=False)
    audit=Mock();monkeypatch.setattr(module,"audit",audit)
    with pytest.raises(SystemExit) as error:activation.main(["paper-audit"])
    assert error.value.code==2;audit.assert_not_called()


@pytest.mark.skipif(os.name!="posix",reason="Native read-only flock probe runs on Linux CI")
def test_read_only_audit_detects_kernel_guard_conflict_without_a_lease(setup):
    layout,_=setup;host=activation.Host(layout)
    with pytest.raises(FileNotFoundError):host.absence_deployment_check()
    assert not (layout.root/".activation.guard").exists()
    with activation.operations._activation_guard(layout.root):
        before=(layout.root/".activation.guard").stat()
        with pytest.raises(BlockingIOError):host.absence_deployment_check()
        assert not (layout.root/".activation.lock").exists()
    assert host.absence_deployment_check()==(0,)
    after=(layout.root/".activation.guard").stat()
    assert (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns)


@pytest.mark.skipif(os.name!="posix",reason="Native inode/permission proof runs on Linux CI")
@pytest.mark.parametrize("kind", ["symlink","writable","hardlink"])
def test_read_only_guard_probe_rejects_untrusted_inode(tmp_path,kind):
    path=tmp_path/".activation.guard"
    if kind=="symlink":
        target=tmp_path/"target";target.write_text("");path.symlink_to(target)
    else:
        path.write_text("")
        if kind=="writable":path.chmod(0o666)
        else:os.link(path,tmp_path/"alias")
    with pytest.raises((ValueError,OSError)):
        with module.deployment_view(tmp_path):pytest.fail("untrusted guard entered")
