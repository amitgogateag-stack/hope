import os
from pathlib import Path
import subprocess
import json

import pytest

from hope.api import operations_deployment as module


@pytest.fixture
def prepared_release(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    for file in ("src/hope/api/paper_service.py", "src/hope/api/dashboard_server.py",
                 "src/hope/api/dashboard_snapshot.py", "docs/dashboard/preview.html",
                 "deploy/operations/hope-paper.service", "migrations/001.sql"):
        path = release / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("reviewed code\n")
    def git(*args):
        return subprocess.run(["git", "-C", str(release), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init")
    git("config", "user.name", "HOPE Test")
    git("config", "user.email", "local-fixture@example.invalid")
    git("remote", "add", "origin", "https://github.com/amitgogateag-stack/hope.git")
    git("add", ".")
    git("commit", "-m", "synthetic deployment fixture")
    return release, git("rev-parse", "HEAD"), git


def test_preflight_accepts_exact_clean_release_without_mutation(prepared_release):
    release, sha, git = prepared_release
    before = git("status", "--porcelain")
    assert module.inspect_release(release, sha)["status"] == "COMPATIBLE_CODE"
    assert git("status", "--porcelain") == before == ""
    assert git("rev-parse", "HEAD") == sha


@pytest.mark.parametrize("failure", ["sha", "dirty", "origin", "nested", "missing"])
def test_preflight_blocks_mismatched_installation(prepared_release, failure):
    release, sha, git = prepared_release
    if failure == "sha":
        sha = "0" * 40
    elif failure == "dirty":
        (release / "unreviewed").write_text("not approved")
    elif failure == "origin":
        git("remote", "set-url", "origin", "https://example.invalid/wrong")
    elif failure == "nested":
        release = release / "src"
    else:
        (release / "src/hope/api/paper_service.py").unlink()
        git("add", "-u")
        git("commit", "-m", "incomplete fixture")
        sha = git("rev-parse", "HEAD")
    with pytest.raises(module.DeploymentError):
        module.inspect_release(release, sha)


@pytest.mark.parametrize("sha", ["", "abc", "A" * 40, "x; echo secret", "1" * 41])
def test_bad_release_identity_never_executes_git(tmp_path, monkeypatch, sha):
    monkeypatch.setattr(module, "_git", lambda *a: pytest.fail("must validate first"))
    with pytest.raises(module.DeploymentError, match="COMMIT_INVALID"):
        module.inspect_release(tmp_path, sha)


@pytest.mark.parametrize("accepted", [True, False])
def test_atomic_release_switch_restores_previous_on_acceptance_failure(tmp_path, monkeypatch, accepted):
    root = tmp_path / "operations"
    old, new = "1" * 40, "2" * 40
    for sha in (old, new):
        (root / "releases" / sha).mkdir(parents=True)
    try:
        os.symlink(root / "releases" / old, root / "current", target_is_directory=True)
    except OSError:
        pytest.skip("Host does not grant symlink creation; exercised on Linux CI")
    monkeypatch.setattr(module, "inspect_release", lambda *a: {"status": "COMPATIBLE_CODE"})
    def accept():
        assert (root / "current").resolve().name == new
        if not accepted:
            raise RuntimeError("synthetic health failure")
    if accepted:
        module.switch_release(root, new, expected_current=old, accept=accept)
    else:
        with pytest.raises(RuntimeError, match="health failure"):
            module.switch_release(root, new, expected_current=old, accept=accept)
    assert (root / "current").resolve().name == (new if accepted else old)
    assert not (root / ".current.next").exists()
    assert (root / "releases" / old).is_dir() and (root / "releases" / new).is_dir()
    with pytest.raises(module.DeploymentError, match="LEASE_MISMATCH"):
        module.switch_release(root, new, expected_current="3" * 40, accept=lambda: pytest.fail("no activation"))


def test_systemd_units_bound_resources_and_enable_reboot_ordering():
    root = Path(__file__).parents[2] / "deploy/operations"
    paper = (root / "hope-paper.service").read_text()
    for required in ("User=hope-paper", "Restart=on-failure", "KillSignal=SIGTERM",
                     "TimeoutStopSec=60s", "WantedBy=multi-user.target", "MemoryMax=1G",
                     "operations_deployment", "--database-component paper", "NoNewPrivileges=yes"):
        assert required in paper
    web = (root / "hope-ops-dashboard-web.service").read_text()
    assert "--port 8788" in web and "--operations-status-file" in web
    assert "EnvironmentFile" not in web
    timer = (root / "hope-ops-dashboard-export.timer").read_text()
    assert "OnBootSec=30s" in timer and "OnUnitInactiveSec=60s" in timer


def test_cli_preflight_sanitizes_repository_and_database_failure(tmp_path, monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "argv", ["preflight", "--release", str(tmp_path), "--expected-sha", "a" * 40,
                                     "--database-component", "paper"])
    monkeypatch.setattr(module, "inspect_release", lambda *args: {"status": "COMPATIBLE_CODE"})
    monkeypatch.delenv("HOPE_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
    assert '"reason": "OPERATIONS_PREFLIGHT_FAILED"' in capsys.readouterr().out
    monkeypatch.setenv("HOPE_DATABASE_URL", "secret-do-not-echo")
    with pytest.raises(SystemExit):
        module.main()
    assert "secret" not in capsys.readouterr().out


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="Requires Linux process identity; exercised on Linux CI")
def test_activation_lock_serializes_complete_operation_and_cleans_after_failure(tmp_path):
    old, new = "1" * 40, "2" * 40
    with module.activation_lock(tmp_path, expected_current=old, target_sha=new):
        record = json.loads((tmp_path / ".activation.lock").read_text())
        assert record["expected_current"] == old and record["target_sha"] == new
        assert record["pid"] == os.getpid() and record["process_start_ticks"].isdigit()
        with pytest.raises(module.DeploymentError, match="ACTIVATION_LOCKED"):
            with module.activation_lock(tmp_path, expected_current=old, target_sha=new):
                pytest.fail("concurrent activation admitted")
        assert (tmp_path / ".activation.lock").is_file()
    assert not (tmp_path / ".activation.lock").exists()
    with pytest.raises(RuntimeError):
        with module.activation_lock(tmp_path, expected_current=old, target_sha=new):
            raise RuntimeError("acceptance failed")
    assert not (tmp_path / ".activation.lock").exists()


def _proc_fixture(root, pid, *, boot="11111111-1111-1111-1111-111111111111", started="42"):
    (root / "sys/kernel/random").mkdir(parents=True)
    (root / "sys/kernel/random/boot_id").write_text(boot)
    (root / str(pid)).mkdir()
    # starttime is field 22: after pid/comm, it is the twentieth token from state.
    (root / str(pid) / "stat").write_text(f"{pid} (worker name) S " + "0 " * 18 + started + " 0\n")


def _interrupted_activation(tmp_path, monkeypatch):
    if not hasattr(os, "geteuid"):
        pytest.skip("Requires POSIX ownership and symlinks; exercised on Linux CI")
    old, new, pid = "1" * 40, "2" * 40, 4321
    releases = tmp_path / "releases"
    (releases / old).mkdir(parents=True)
    (releases / new).mkdir()
    try:
        os.symlink(releases / old, tmp_path / "current", target_is_directory=True)
    except OSError:
        pytest.skip("Host does not grant symlink creation; exercised on Linux CI")
    (tmp_path / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={old}\n")
    proc = tmp_path / "proc"
    _proc_fixture(proc, pid)
    record = {"schema_version": 1, "pid": pid,
              "boot_id": "11111111-1111-1111-1111-111111111111",
              "process_start_ticks": "42", "expected_current": old, "target_sha": new}
    (tmp_path / ".activation.lock").write_text(json.dumps(record))
    monkeypatch.setattr(module.os, "geteuid", lambda: (tmp_path / ".activation.lock").stat().st_uid)
    return old, new, pid, proc


def test_recovery_inspection_authenticates_active_process_identity(tmp_path, monkeypatch):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    result = module.inspect_activation_recovery(tmp_path, proc_root=proc)
    assert result == {"schema_version": 1, "state": "ACTIVE", "expected_current": old,
                      "target_sha": new, "current_pointer": "EXPECTED",
                      "deployed_metadata": "EXPECTED", "pending_pointer": "ABSENT",
                      "recovery_safe": False}
    # PID reuse is stale because the immutable process-start identity changed.
    (proc / str(pid) / "stat").write_text(f"{pid} (reused) S " + "0 " * 18 + "99 0\n")
    result = module.inspect_activation_recovery(tmp_path, proc_root=proc)
    assert result["state"] == "STALE" and result["recovery_safe"] is True


@pytest.mark.parametrize("pointer,metadata,pending", [
    ("TARGET", "EXPECTED", False), ("EXPECTED", "TARGET", False),
    ("TARGET", "TARGET", False), ("EXPECTED", "EXPECTED", True),
])
def test_stale_activation_requires_review_for_every_ambiguous_state(
    tmp_path, monkeypatch, pointer, metadata, pending,
):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    (proc / str(pid) / "stat").unlink()
    (tmp_path / "current").unlink()
    os.symlink(tmp_path / "releases" / (new if pointer == "TARGET" else old),
               tmp_path / "current", target_is_directory=True)
    (tmp_path / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={new if metadata == 'TARGET' else old}\n")
    if pending:
        os.symlink(tmp_path / "releases" / new, tmp_path / ".current.next", target_is_directory=True)
    result = module.inspect_activation_recovery(tmp_path, proc_root=proc)
    assert result["state"] == "STALE" and result["recovery_safe"] is False
    assert result["current_pointer"] == pointer and result["deployed_metadata"] == metadata


@pytest.mark.parametrize("payload", ["not-json", "{}", '{"schema_version":1}', "[]"])
@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Requires POSIX lock ownership; exercised on Linux CI")
def test_recovery_inspection_rejects_untrusted_or_malformed_lock(tmp_path, payload):
    (tmp_path / ".activation.lock").write_text(payload)
    with pytest.raises(module.DeploymentError, match="LOCK_INVALID"):
        module.inspect_activation_recovery(tmp_path)


def test_safe_stale_recovery_stops_services_and_removes_only_unchanged_lock(tmp_path, monkeypatch):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    (proc / str(pid) / "stat").unlink()
    before_pointer = os.readlink(tmp_path / "current")
    before_metadata = (tmp_path / "deployed.env").read_bytes()
    calls = []
    result = module.recover_stale_activation(
        tmp_path, expected_current=old, target_sha=new,
        quiesce=lambda: calls.append("stopped"), proc_root=proc,
    )
    assert result == {"schema_version": 1, "state": "RECOVERED", "services": "STOPPED",
                      "expected_current": old, "target_sha": new}
    assert calls == ["stopped"] and not (tmp_path / ".activation.lock").exists()
    assert os.readlink(tmp_path / "current") == before_pointer
    assert (tmp_path / "deployed.env").read_bytes() == before_metadata


@pytest.mark.parametrize("failure", ["active", "expected", "target", "mixed", "pending"])
def test_stale_recovery_refuses_every_unproven_state_before_stopping(tmp_path, monkeypatch, failure):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    if failure != "active":
        (proc / str(pid) / "stat").unlink()
    expected_arg, target_arg = old, new
    if failure == "expected":
        expected_arg = "3" * 40
    elif failure == "target":
        target_arg = "3" * 40
    elif failure == "mixed":
        (tmp_path / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={new}\n")
    elif failure == "pending":
        os.symlink(tmp_path / "releases" / new, tmp_path / ".current.next")
    with pytest.raises(module.DeploymentError, match="RECOVERY_DENIED"):
        module.recover_stale_activation(
            tmp_path, expected_current=expected_arg, target_sha=target_arg,
            quiesce=lambda: pytest.fail("unsafe state must not stop services"), proc_root=proc,
        )
    assert (tmp_path / ".activation.lock").exists()


def test_stale_recovery_retains_lock_when_stop_fails_or_state_changes(tmp_path, monkeypatch):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    (proc / str(pid) / "stat").unlink()
    with pytest.raises(RuntimeError, match="stop failed"):
        module.recover_stale_activation(
            tmp_path, expected_current=old, target_sha=new,
            quiesce=lambda: (_ for _ in ()).throw(RuntimeError("stop failed")), proc_root=proc,
        )
    assert (tmp_path / ".activation.lock").exists()

    def change_state():
        (tmp_path / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={new}\n")
    with pytest.raises(module.DeploymentError, match="STATE_CHANGED"):
        module.recover_stale_activation(
            tmp_path, expected_current=old, target_sha=new,
            quiesce=change_state, proc_root=proc,
        )
    assert (tmp_path / ".activation.lock").exists()
