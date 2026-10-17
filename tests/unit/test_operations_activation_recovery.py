import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    path = Path(__file__).parents[2] / "deploy/operations/recover_activation.py"
    spec = importlib.util.spec_from_file_location("m1_activation_recovery_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    monkeypatch.setattr(module.os, "geteuid", lambda: 0, raising=False)
    return module


def test_operator_recovery_requires_exact_identity_stops_and_verifies_every_unit(
    recovery, monkeypatch, capsys,
):
    old, target = "1" * 40, "2" * 40
    monkeypatch.setattr(sys, "argv", ["recover", "--expected-current", old,
                                     "--target", target, "--approve-host-change"])
    commands, invocation = [], {}
    def systemctl(*args):
        commands.append(args)
        return "inactive" if args[0] == "show" else ""
    monkeypatch.setattr(recovery, "systemctl", systemctl)
    def recover(root, *, expected_current, target_sha, quiesce):
        invocation.update(root=root, expected=expected_current, target=target_sha)
        quiesce()
    monkeypatch.setattr(recovery, "recover_stale_activation", recover)
    recovery.main()
    assert invocation == {"root": recovery.ROOT, "expected": old, "target": target}
    assert commands[0] == ("stop", *recovery.UNITS)
    assert commands[1:] == [("show", "--property=ActiveState", "--value", unit)
                            for unit in recovery.UNITS]
    assert "SERVICES_STOPPED" in capsys.readouterr().out


def test_operator_recovery_refuses_non_root_and_non_quiescent_unit(recovery, monkeypatch):
    old, target = "1" * 40, "2" * 40
    monkeypatch.setattr(sys, "argv", ["recover", "--expected-current", old,
                                     "--target", target, "--approve-host-change"])
    monkeypatch.setattr(recovery.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit) as exc:
        recovery.main()
    assert exc.value.code == 2

    monkeypatch.setattr(recovery.os, "geteuid", lambda: 0)
    monkeypatch.setattr(recovery, "systemctl", lambda *args: "active" if args[0] == "show" else "")
    def recover(root, **kwargs):
        kwargs["quiesce"]()
    monkeypatch.setattr(recovery, "recover_stale_activation", recover)
    with pytest.raises(RuntimeError, match="NOT_STOPPED"):
        recovery.main()
