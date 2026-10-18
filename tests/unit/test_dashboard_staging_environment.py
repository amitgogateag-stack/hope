import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
RUNBOOK = ROOT / "docs/m3-dashboard-only-activation.md"


def _run(command, *, env=None):
    return subprocess.run(command, check=True, capture_output=True, text=True, env=env)


def _git(path, *args, env=None, safe=False):
    command = ["git"]
    if safe:
        command += ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    return _run([*command, "-C", str(path), *args], env=env)


def _seed(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q")
    _git(seed, "config", "user.email", "proof@example.invalid")
    _git(seed, "config", "user.name", "proof")
    (seed / "reviewed.txt").write_text("reviewed\n")
    _git(seed, "add", "reviewed.txt")
    _git(seed, "commit", "-qm", "reviewed")
    return seed


def test_aws_staging_clears_ambient_git_execution_inputs(tmp_path):
    seed = _seed(tmp_path)
    hostile_home = tmp_path / "hostile-home"
    hooks = hostile_home / "hooks"
    hooks.mkdir(parents=True)
    marker = tmp_path / "ambient-hook-ran"
    hook = hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\nprintf executed > {marker}\n")
    hook.chmod(0o755)
    (hostile_home / ".gitconfig").write_text(f"[core]\n\thooksPath = {hooks}\n")

    ambient = dict(os.environ, HOME=str(hostile_home))
    unsafe = tmp_path / "unsafe-release"
    _run(["git", "clone", "-q", "--no-hardlinks", "--no-checkout", str(seed), str(unsafe)], env=ambient)
    _git(unsafe, "checkout", "-q", "--detach", "HEAD", env=ambient)
    assert marker.read_text() == "executed"

    marker.unlink()
    safe_env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}
    safe = tmp_path / "safe-release"
    _run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
        "clone", "-q", "--no-hardlinks", "--no-checkout", str(seed), str(safe)], env=safe_env)
    _git(safe, "checkout", "-q", "--detach", "HEAD", env=safe_env, safe=True)
    assert not marker.exists()
    assert _git(safe, "status", "--porcelain", "--untracked-files=all",
        env=safe_env, safe=True).stdout == ""


def test_runbook_requires_safe_git_for_every_privileged_staging_command():
    content = RUNBOOK.read_text()
    staging = content.split("## Prepare the reviewed target", 1)[1].split(
        "## Read-only preflight", 1)[0]
    assert "SAFE_GIT=(env -i PATH=/usr/bin:/bin HOME=/nonexistent" in staging
    assert "GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 GIT_PAGER=cat" in staging
    assert "git -c core.hooksPath=/dev/null -c core.fsmonitor=false" in staging
    assert staging.count('sudo "${SAFE_GIT[@]}"') == 6
    assert "sudo git " not in staging
