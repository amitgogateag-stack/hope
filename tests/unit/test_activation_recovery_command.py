import importlib.util
import json
import os
from pathlib import Path
import pytest


def _module():
    path = Path(__file__).parents[2] / "src/hope/api/activation_recovery.py"
    spec = importlib.util.spec_from_file_location("activation_recovery_command", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_command_reports_safe_absence_without_mutation(tmp_path, capsys):
    command = _module()
    command.ROOT = tmp_path
    before = set(tmp_path.iterdir())
    assert command.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1, "state": "ABSENT", "recovery_safe": True,
    }
    assert set(tmp_path.iterdir()) == before


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Requires POSIX lock ownership; exercised on Linux CI")
def test_command_sanitizes_untrusted_lock_and_fails_closed(tmp_path, capsys):
    command = _module()
    command.ROOT = tmp_path
    (tmp_path / ".activation.lock").write_text("credential=do-not-leak")
    assert command.main() == 2
    output = capsys.readouterr().out
    assert "do-not-leak" not in output
    assert json.loads(output)["state"] == "UNTRUSTED"
