"""Privilege-boundary contracts for the retained M1 operator scripts."""
from pathlib import Path
import os
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
SCRIPTS = [
    ROOT / "deploy/operations/preflight.sh",
    ROOT / "deploy/operations/install_operations.sh",
]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_privileged_scripts_reexec_with_a_minimal_environment_before_work(script):
    content = script.read_text()
    reset = content.index("exec /usr/bin/env -i")
    validation = content.index("[[ $# == 1") if script.name == "preflight.sh" else content.index("[[ ${EUID}")
    assert reset < validation
    assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C" in content
    assert "HOPE_OPERATIONS_SANITIZED=1 /usr/bin/bash \"$0\" \"$@\"" in content
    assert "OPERATIONS_ENVIRONMENT_ISOLATION_FAILED" in content
    assert "PWD|SHLVL|_|PATH|LANG|LC_ALL|HOPE_OPERATIONS_SANITIZED" in content
    assert "sudo -E" not in content and "--preserve-env" not in content


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_privileged_scripts_do_not_path_resolve_external_commands(script):
    commands = {
        "bash", "curl", "env", "find", "git", "id", "install", "readlink", "rm",
        "runuser", "ss", "stat", "systemctl", "systemd-analyze", "uname",
    }
    executable = "\n".join(
        line for line in script.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    for command in commands:
        command_position = rf"(?:^|\n|;\s*(?:do\s+)?|\$\()\s*{re.escape(command)}(?=\s|$)"
        assert re.search(command_position, executable) is None
    assert "command -v" not in executable


def test_preflight_python_and_systemd_children_have_explicit_clean_environments():
    content = SCRIPTS[0].read_text()
    assert content.count("/usr/bin/env -i") == 3
    assert "for command in /usr/bin/curl /usr/bin/git" in content
    assert 'PYTHONPATH="$ROOT/current/src" "$ROOT/venv/bin/python" -B -s -P' in content
    assert "-m hope.api.operations_deployment" in content
    assert "/usr/bin/systemd-analyze verify" in content
    assert "export PYTHONPATH" not in content


def test_installer_systemctl_calls_are_fixed_and_environment_isolated():
    content = SCRIPTS[1].read_text()
    assert content.count("/usr/bin/systemctl daemon-reload") == 2
    assert content.count("/usr/bin/env -i") == 3
    assert "/usr/bin/install -m 0644" in content
    assert "/usr/bin/rm -f --" in content


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash syntax verified in Linux CI")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_privileged_scripts_remain_valid_bash(script):
    subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True, timeout=5)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
@pytest.mark.skipif(os.name != "posix", reason="Native /usr/bin/bash environment isolation runs on Linux CI")
def test_spoofed_sanitization_marker_cannot_bypass_environment_guard(script):
    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env={
            "PATH": "/hostile", "LANG": "C", "LC_ALL": "C",
            "HOPE_OPERATIONS_SANITIZED": "1", "UNRELATED_SECRET": "must-not-pass",
        },
        text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 2
    assert result.stderr.strip() == "OPERATIONS_ENVIRONMENT_ISOLATION_FAILED"


@pytest.mark.parametrize(
    ("script", "message"),
    [
        (SCRIPTS[0], "OPERATIONS_EXPECTED_SHA_REQUIRED"),
        (SCRIPTS[1], "Usage: sudo bash install_operations.sh APPROVED_SHA"),
    ],
    ids=("preflight", "installer"),
)
@pytest.mark.skipif(os.name != "posix", reason="Native /usr/bin/bash environment isolation runs on Linux CI")
def test_hostile_caller_environment_is_scrubbed_before_argument_validation(script, message):
    result = subprocess.run(
        ["/usr/bin/bash", str(script)],
        env={
            "PATH": "/hostile", "LANG": "hostile", "LC_ALL": "C",
            "PYTHONPATH": "/hostile/python", "LD_PRELOAD": "/does/not/exist",
            "UNRELATED_SECRET": "must-not-pass",
        },
        text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 2
    assert result.stderr.strip().endswith(message)
    assert "OPERATIONS_ENVIRONMENT_ISOLATION_FAILED" not in result.stderr
