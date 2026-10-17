import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from hope.api import dashboard_deployment as deployment


def _release_tree(root: Path, sha: str) -> Path:
    release = root / "releases" / sha
    release.mkdir(parents=True)
    return release


def _prepared(tmp_path, monkeypatch):
    old, target = "1" * 40, "2" * 40
    _release_tree(tmp_path, old)
    _release_tree(tmp_path, target)
    monkeypatch.setattr(deployment, "inspect_release", lambda *args: {"status": "COMPATIBLE_CODE"})
    return old, target


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="Requires symlinks; exercised on Linux CI")
def test_dashboard_switch_uses_dedicated_pointer_and_rolls_back_without_shared_current(tmp_path, monkeypatch):
    old, target = _prepared(tmp_path, monkeypatch)
    os.symlink(tmp_path / "releases" / old, tmp_path / "current", target_is_directory=True)
    shared = os.readlink(tmp_path / "current")
    seen = []

    def fail_accept():
        seen.append((tmp_path / deployment.POINTER).resolve().name)
        raise RuntimeError("synthetic dashboard failure")

    with pytest.raises(RuntimeError, match="dashboard failure"):
        deployment.switch_dashboard_release(tmp_path, target, expected_current=old,
                                            before_switch=lambda: seen.append("stopped-dashboard-only"),
                                            accept=fail_accept)
    assert seen == ["stopped-dashboard-only", target]
    assert (tmp_path / deployment.POINTER).resolve().name == old
    assert (tmp_path / deployment.METADATA).read_text() == f"HOPE_DASHBOARD_DEPLOYED_COMMIT={old}\n"
    assert os.readlink(tmp_path / "current") == shared
    assert not (tmp_path / deployment.LOCK).exists()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="Requires symlinks; exercised on Linux CI")
def test_dashboard_switch_accepts_target_and_rejects_wrong_pointer_or_concurrency(tmp_path, monkeypatch):
    old, target = _prepared(tmp_path, monkeypatch)
    deployment.switch_dashboard_release(tmp_path, target, expected_current=old,
                                        accept=lambda: None)
    assert (tmp_path / deployment.POINTER).resolve().name == target
    assert (tmp_path / deployment.METADATA).read_text() == f"HOPE_DASHBOARD_DEPLOYED_COMMIT={target}\n"
    with pytest.raises(deployment.DeploymentError, match="LEASE_MISMATCH"):
        deployment.switch_dashboard_release(tmp_path, target, expected_current=old, accept=lambda: None)

    # The dedicated lease does not reuse or overwrite the full-release lease.
    (tmp_path / deployment.LOCK).write_text("occupied")
    with pytest.raises(deployment.DeploymentError, match="ACTIVATION_LOCKED"):
        with deployment.dashboard_activation_lock(tmp_path, expected_current=old, target_sha=target):
            pytest.fail("concurrent activation admitted")


def _proc_fixture(root: Path, pid: int, started: str = "42") -> None:
    (root / "sys/kernel/random").mkdir(parents=True)
    (root / "sys/kernel/random/boot_id").write_text("11111111-1111-1111-1111-111111111111")
    (root / str(pid)).mkdir()
    (root / str(pid) / "stat").write_text(f"{pid} (dashboard worker) S " + "0 " * 18 + started + " 0\n")


def _interrupted(tmp_path, monkeypatch, *, at_target: bool):
    old, target, pid = "1" * 40, "2" * 40, 4321
    for sha in (old, target):
        _release_tree(tmp_path, sha)
    os.symlink(tmp_path / "releases" / (target if at_target else old), tmp_path / deployment.POINTER)
    (tmp_path / deployment.METADATA).write_text(
        f"HOPE_DASHBOARD_DEPLOYED_COMMIT={target if at_target else old}\n")
    proc = tmp_path / "proc"
    _proc_fixture(proc, pid)
    record = {"schema_version": 1, "pid": pid,
              "boot_id": "11111111-1111-1111-1111-111111111111",
              "process_start_ticks": "42", "expected_current": old, "target_sha": target}
    (tmp_path / deployment.LOCK).write_text(json.dumps(record))
    monkeypatch.setattr(deployment.os, "geteuid", lambda: (tmp_path / deployment.LOCK).stat().st_uid)
    return old, target, pid, proc


@pytest.mark.parametrize("at_target", [False, True])
def test_stale_dashboard_recovery_restores_expected_pointer_from_pre_or_post_switch(tmp_path, monkeypatch, at_target):
    old, target, pid, proc = _interrupted(tmp_path, monkeypatch, at_target=at_target)
    (proc / str(pid) / "stat").unlink()
    calls = []
    result = deployment.recover_dashboard_activation(
        tmp_path, expected_current=old, target_sha=target,
        quiesce=lambda: calls.append("dashboard-stopped"), proc_root=proc)
    assert result["state"] == "RECOVERED" and calls == ["dashboard-stopped"]
    assert (tmp_path / deployment.POINTER).resolve().name == old
    assert (tmp_path / deployment.METADATA).read_text() == f"HOPE_DASHBOARD_DEPLOYED_COMMIT={old}\n"
    assert not (tmp_path / deployment.LOCK).exists()


@pytest.mark.parametrize("failure", ["active", "wrong-target", "unknown-pending", "other-pointer"])
def test_dashboard_recovery_refuses_unproven_interruption_before_quiescence(tmp_path, monkeypatch, failure):
    old, target, pid, proc = _interrupted(tmp_path, monkeypatch, at_target=False)
    if failure != "active":
        (proc / str(pid) / "stat").unlink()
    target_arg = "3" * 40 if failure == "wrong-target" else target
    if failure == "unknown-pending":
        outside_pending = tmp_path / "outside-pending"
        outside_pending.mkdir()
        os.symlink(outside_pending, tmp_path / deployment.PENDING)
    if failure == "other-pointer":
        (tmp_path / deployment.POINTER).unlink()
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, tmp_path / deployment.POINTER)
    with pytest.raises(deployment.DeploymentError, match="RECOVERY_DENIED"):
        deployment.recover_dashboard_activation(
            tmp_path, expected_current=old, target_sha=target_arg,
            quiesce=lambda: pytest.fail("unsafe recovery reached service mutation"), proc_root=proc)


def test_stale_recovery_removes_authenticated_interrupted_pending_pointer(tmp_path, monkeypatch):
    old, target, pid, proc = _interrupted(tmp_path, monkeypatch, at_target=False)
    (proc / str(pid) / "stat").unlink()
    os.symlink(tmp_path / "releases" / target, tmp_path / deployment.PENDING)
    deployment.recover_dashboard_activation(
        tmp_path, expected_current=old, target_sha=target, quiesce=lambda: None, proc_root=proc)
    assert not (tmp_path / deployment.PENDING).exists()
    assert (tmp_path / deployment.POINTER).resolve().name == old


@pytest.fixture
def operator(tmp_path, monkeypatch):
    path = Path(__file__).parents[2] / "deploy/operations/activate_dashboard_release.py"
    spec = importlib.util.spec_from_file_location("dashboard_activation_operator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.STATE = tmp_path / "state"
    module.STATE.mkdir()
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "inspect_release", lambda *args: {"status": "COMPATIBLE_CODE"})
    monkeypatch.setattr(module, "_verified_snapshot", lambda *args: (b"{}", None))
    return module


def test_operator_switches_only_dashboard_units_and_requires_acceptance(operator, monkeypatch):
    old, target = "1" * 40, "2" * 40
    commands = []
    def systemctl(*args):
        commands.append(args)
        if args == ("is-enabled", operator.LEGACY_UNIT):
            return "enabled"
        return ""
    monkeypatch.setattr(operator, "systemctl", systemctl)
    monkeypatch.setattr(operator, "_install_exact_unit", lambda *args: None)
    monkeypatch.setattr(operator, "_accepted", lambda *args: True)
    def switch(root, target_sha, *, expected_current, before_switch, accept):
        assert (target_sha, expected_current) == (target, old)
        before_switch()
        accept()
    monkeypatch.setattr(operator, "switch_dashboard_release", switch)
    monkeypatch.setattr(sys, "argv", ["activate-dashboard", "--target", target,
                                      "--expected-current", old,
                                      "--approve-dashboard-only-host-change"])
    operator.main()
    flattened = " ".join(" ".join(call) for call in commands)
    assert "hope-paper" not in flattened
    assert ("stop", operator.UNIT, operator.LEGACY_UNIT) in commands
    assert ("enable", operator.UNIT) in commands
    assert ("start", operator.UNIT) in commands


def test_operator_failure_restores_legacy_service_without_paper_commands(operator, monkeypatch):
    old, target = "1" * 40, "2" * 40
    commands = []
    monkeypatch.setattr(operator, "systemctl", lambda *args: commands.append(args) or
                        ("enabled" if args == ("is-enabled", operator.LEGACY_UNIT) else ""))
    monkeypatch.setattr(operator, "_install_exact_unit", lambda *args: None)
    monkeypatch.setattr(operator, "_accepted", lambda *args: False)
    monkeypatch.setattr(operator.time, "sleep", lambda *_: None)
    def switch(root, target_sha, *, expected_current, before_switch, accept):
        before_switch()
        accept()
    monkeypatch.setattr(operator, "switch_dashboard_release", switch)
    monkeypatch.setattr(sys, "argv", ["activate-dashboard", "--target", target,
                                      "--expected-current", old,
                                      "--approve-dashboard-only-host-change"])
    with pytest.raises(RuntimeError, match="ACCEPTANCE_TIMEOUT"):
        operator.main()
    assert ("disable", operator.UNIT) in commands
    assert ("enable", operator.LEGACY_UNIT) in commands
    assert ("start", operator.LEGACY_UNIT) in commands
    assert all("hope-paper" not in " ".join(command) for command in commands)


def test_installed_unit_must_exactly_match_reviewed_target(operator, monkeypatch, tmp_path):
    target = tmp_path / "target"
    source = target / "deploy/operations" / operator.UNIT
    source.parent.mkdir(parents=True)
    source.write_text("reviewed unit\n")
    installed = tmp_path / "installed.service"
    installed.write_text("tampered unit\n")
    monkeypatch.setattr(operator, "UNIT_PATH", installed)
    with pytest.raises(RuntimeError, match="INSTALLED_UNIT_MISMATCH"):
        operator._install_exact_unit(target)


def test_recovery_operator_stops_only_dashboards_then_restores_legacy(tmp_path, monkeypatch):
    path = Path(__file__).parents[2] / "deploy/operations/recover_dashboard_activation.py"
    spec = importlib.util.spec_from_file_location("dashboard_recovery_operator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    old, target = "1" * 40, "2" * 40
    commands, invocation = [], {}
    def systemctl(*args):
        commands.append(args)
        return "inactive" if args[0] == "show" else ""
    monkeypatch.setattr(module, "systemctl", systemctl)
    def recover(root, *, expected_current, target_sha, quiesce):
        invocation.update(root=root, expected=expected_current, target=target_sha)
        quiesce()
    monkeypatch.setattr(module, "recover_dashboard_activation", recover)
    monkeypatch.setattr(sys, "argv", ["recover-dashboard", "--expected-current", old,
                                      "--target", target, "--approve-dashboard-recovery"])
    module.main()
    assert invocation == {"root": tmp_path, "expected": old, "target": target}
    assert ("stop", module.NEW, module.LEGACY) in commands
    assert ("disable", module.NEW) in commands
    assert ("enable", module.LEGACY) in commands
    assert ("start", module.LEGACY) in commands
    assert all("hope-paper" not in " ".join(command) for command in commands)


def test_dashboard_unit_uses_dedicated_pointer_loopback_and_no_paper_activation():
    path = Path(__file__).parents[2] / "deploy/operations/hope-m3-dashboard-web.service"
    unit = path.read_text()
    assert "/opt/hope-operations/dashboard-current" in unit
    assert "/opt/hope-operations/current" not in unit
    assert "--port 8788" in unit and "User=hope-dashboard" in unit
    assert "hope-paper.service" not in unit
