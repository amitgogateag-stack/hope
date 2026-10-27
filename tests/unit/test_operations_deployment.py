import os
from pathlib import Path
import subprocess
import json
import shutil
from types import SimpleNamespace

import pytest

from hope.api import operations_deployment as module


@pytest.fixture
def prepared_release(tmp_path, monkeypatch):
    if os.name != "posix":
        # Exercise real Git metadata on Windows; Linux retains the fixed
        # production binary and environment without any adapter.
        monkeypatch.setattr(module, "GIT_BINARY", shutil.which("git"))
        monkeypatch.setattr(module, "GIT_ENVIRONMENT", {**module.GIT_ENVIRONMENT,
            "PATH":os.environ["PATH"], "SystemRoot":os.environ.get("SystemRoot", "")})
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
    # Use the same line-ending policy under ambient and isolated Git. Otherwise
    # Windows global autocrlf can make this synthetic checkout appear dirty
    # only when the production inspector correctly ignores global config.
    git("config", "core.autocrlf", "false")
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


@pytest.mark.skipif(os.name!="posix",reason="Native privileged Git isolation; exercised in Linux CI")
def test_release_inspection_ignores_hostile_path_global_config_and_fsmonitor(prepared_release,tmp_path,monkeypatch):
    release,sha,git=prepared_release
    marker=tmp_path/"executed"
    hostile=tmp_path/"hostile";hostile.mkdir()
    hook=hostile/"monitor"
    hook.write_text("#!/bin/sh\nprintf executed > '"+str(marker)+"'\n")
    hook.chmod(0o700)
    fake=hostile/"git";fake.write_text(hook.read_text());fake.chmod(0o700)
    config=hostile/"config";config.write_text("[core]\nfsmonitor = "+str(hook)+"\n")
    git("config","core.fsmonitor",str(hook))
    monkeypatch.setenv("PATH",str(hostile));monkeypatch.setenv("GIT_CONFIG_GLOBAL",str(config))
    assert module.inspect_release(release,sha)["status"]=="COMPATIBLE_CODE"
    assert not marker.exists()


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


@pytest.mark.skipif(os.name != "posix", reason="Linux descriptor-bound release switch")
def test_release_switch_rejects_root_replaced_after_preflight(tmp_path, monkeypatch):
    root = tmp_path / "operations"
    displaced = tmp_path / "displaced"
    old, new = "1" * 40, "2" * 40
    for sha in (old, new):
        (root / "releases" / sha).mkdir(parents=True)
    os.symlink(root / "releases" / old, root / "current", target_is_directory=True)
    monkeypatch.setattr(module, "inspect_release", lambda *a: {"status": "COMPATIBLE_CODE"})

    def replace_root():
        root.rename(displaced)
        root.mkdir()

    with pytest.raises(module.DeploymentError, match="ACTIVATION_ROOT_CHANGED"):
        module.switch_release(
            root, new, expected_current=old,
            before_switch=replace_root,
            accept=lambda: pytest.fail("acceptance ran against a replaced deployment root"),
        )
    assert not (root / "current").exists()
    assert os.readlink(displaced / "current") == str(root / "releases" / old)
    assert not (displaced / ".activation.lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="Linux descriptor-bound release rollback")
def test_release_switch_rolls_back_if_root_is_replaced_during_acceptance(tmp_path, monkeypatch):
    root = tmp_path / "operations"
    displaced = tmp_path / "displaced"
    old, new = "1" * 40, "2" * 40
    for sha in (old, new):
        (root / "releases" / sha).mkdir(parents=True)
    os.symlink(root / "releases" / old, root / "current", target_is_directory=True)
    monkeypatch.setattr(module, "inspect_release", lambda *a: {"status": "COMPATIBLE_CODE"})

    def replace_root_during_acceptance():
        root.rename(displaced)
        root.mkdir()

    with pytest.raises(module.DeploymentError, match="ACTIVATION_ROOT_CHANGED"):
        module.switch_release(
            root, new, expected_current=old,
            accept=replace_root_during_acceptance,
        )
    assert not (root / "current").exists()
    assert os.readlink(displaced / "current") == str(root / "releases" / old)
    assert not (displaced / ".activation.lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="Linux directory durability; exercised in Linux CI")
def test_release_pointer_is_directory_synced_before_acceptance(tmp_path, monkeypatch):
    root = tmp_path / "operations"
    old, new = "1" * 40, "2" * 40
    for sha in (old, new):
        (root / "releases" / sha).mkdir(parents=True)
    os.symlink(root / "releases" / old, root / "current", target_is_directory=True)
    monkeypatch.setattr(module, "inspect_release", lambda *a: {"status": "COMPATIBLE_CODE"})
    synced = []

    def observe_sync(directory):
        synced.append((directory / "current").resolve().name)

    monkeypatch.setattr(module, "_sync_activation_directory", observe_sync)
    module._switch_release(root, new, expected_current=old,
                           accept=lambda: synced.append("accepted"),
                           before_switch=lambda: None)
    assert synced == [new, "accepted"]


@pytest.mark.skipif(os.name != "posix", reason="Linux directory durability; exercised in Linux CI")
def test_release_pointer_sync_failure_restores_durable_previous_pointer(tmp_path, monkeypatch):
    root = tmp_path / "operations"
    old, new = "1" * 40, "2" * 40
    for sha in (old, new):
        (root / "releases" / sha).mkdir(parents=True)
    os.symlink(root / "releases" / old, root / "current", target_is_directory=True)
    monkeypatch.setattr(module, "inspect_release", lambda *a: {"status": "COMPATIBLE_CODE"})
    calls = []

    def fail_first_sync(directory):
        calls.append((directory / "current").resolve().name)
        if len(calls) == 1:
            raise module.DeploymentError("OPERATIONS_ACTIVATION_LEASE_DURABILITY_FAILED")

    monkeypatch.setattr(module, "_sync_activation_directory", fail_first_sync)
    with pytest.raises(module.DeploymentError, match="LEASE_DURABILITY_FAILED"):
        module._switch_release(root, new, expected_current=old,
                               accept=lambda: pytest.fail("unsynced pointer must not be accepted"),
                               before_switch=lambda: None)
    assert calls == [new, old]
    assert (root / "current").resolve().name == old
    assert not (root / ".current.next").exists()


@pytest.mark.skipif(os.name != "posix", reason="Linux symlink identity; exercised in Linux CI")
def test_failed_pointer_rename_preserves_replacement_temporary_symlink(tmp_path, monkeypatch):
    root = tmp_path / "operations"
    old, new = "1" * 40, "2" * 40
    for sha in (old, new):
        (root / "releases" / sha).mkdir(parents=True)
    os.symlink(root / "releases" / old, root / "current", target_is_directory=True)
    monkeypatch.setattr(module, "inspect_release", lambda *a: {"status": "COMPATIBLE_CODE"})
    original_replace = os.replace
    replacement = root / "independent-release-pointer"
    os.symlink(root / "releases" / old, replacement, target_is_directory=True)

    def replace_with_independent_symlink(source, destination):
        if Path(source) == root / ".current.next":
            Path(source).unlink()
            os.symlink(replacement, source)
            raise OSError("synthetic rename failure")
        return original_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", replace_with_independent_symlink)
    with pytest.raises(module.DeploymentError, match="^OPERATIONS_SWITCH_TEMP_CHANGED$"):
        module._switch_release(root, new, expected_current=old,
                               accept=lambda: pytest.fail("rename failed"),
                               before_switch=lambda: None)
    assert (root / ".current.next").is_symlink()
    assert os.readlink(root / ".current.next") == str(replacement)
    assert (root / "current").resolve().name == old


@pytest.mark.skipif(os.name != "posix", reason="Linux symlink identity; exercised in Linux CI")
def test_failed_pointer_rename_preserves_authenticated_pending_symlink(tmp_path, monkeypatch):
    root = tmp_path / "operations"
    old, new = "1" * 40, "2" * 40
    for sha in (old, new):
        (root / "releases" / sha).mkdir(parents=True)
    os.symlink(root / "releases" / old, root / "current", target_is_directory=True)
    monkeypatch.setattr(module, "inspect_release", lambda *a: {"status": "COMPATIBLE_CODE"})
    monkeypatch.setattr(module.os, "replace",
                        lambda *a: (_ for _ in ()).throw(OSError("synthetic rename failure")))

    with pytest.raises(module.DeploymentError, match="^OPERATIONS_SWITCH_TEMP_PRESERVED$"):
        module._switch_release(root, new, expected_current=old,
                               accept=lambda: pytest.fail("rename failed"),
                               before_switch=lambda: None)
    assert (root / ".current.next").is_symlink()
    assert os.readlink(root / ".current.next") == str(root / "releases" / new)
    assert (root / "current").resolve().name == old


def test_systemd_units_bound_resources_and_enable_reboot_ordering():
    root = Path(__file__).parents[2] / "deploy/operations"
    paper = (root / "hope-paper.service").read_text()
    for required in ("User=hope-paper", "Type=notify", "NotifyAccess=main", "WatchdogSec=15min",
                     "Restart=always", "KillSignal=SIGTERM",
                     "TimeoutStartSec=15min", "TimeoutStopSec=60s", "WantedBy=multi-user.target", "MemoryMax=1G",
                     "operations_deployment", "--database-component paper", "NoNewPrivileges=yes"):
        assert required in paper
    web = (root / "hope-ops-dashboard-web.service").read_text()
    assert "--port 8788" in web and "--operations-status-file" in web
    assert "EnvironmentFile" not in web
    timer = (root / "hope-ops-dashboard-export.timer").read_text()
    assert "OnBootSec=30s" in timer and "OnUnitInactiveSec=60s" in timer



def test_paper_worker_cannot_remain_down_after_unexpected_clean_exit():
    paper = (
        Path(__file__).parents[2] / "deploy/operations/hope-paper.service"
    ).read_text()
    restart_lines = [
        line for line in paper.splitlines() if line.startswith("Restart=")
    ]
    assert restart_lines == ["Restart=always"]
    assert "StartLimitIntervalSec=300" in paper
    assert "StartLimitBurst=5" in paper


@pytest.mark.parametrize("name,python_path", [
    ("hope-paper.service", "/opt/hope-operations/current/src:/opt/hope-operations/config"),
    ("hope-ops-dashboard-export.service", "/opt/hope-operations/current/src"),
    ("hope-ops-dashboard-web.service", "/opt/hope-operations/current/src"),
    ("hope-m3-dashboard-web.service", "/opt/hope-operations/dashboard-current/src"),
])
def test_python_units_ignore_ambient_python_or_loader_paths(name, python_path):
    unit = (Path(__file__).parents[2] / "deploy/operations" / name).read_text()
    unset = next(line for line in unit.splitlines() if line.startswith("UnsetEnvironment="))
    for variable in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONWARNINGS",
                     "PYTHONINSPECT", "PYTHONSTARTUP", "PYTHONBREAKPOINT",
                     "PYTHONPLATLIBDIR", "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT"):
        assert variable in unset.split("=", 1)[1].split()
    assert "Environment=PYTHONPATH=" not in unit
    python_commands = [line for line in unit.splitlines()
                       if line.startswith(("ExecStart=", "ExecStartPre=")) and "/python " in line]
    assert python_commands
    assert all(line.split("=", 1)[1].startswith(f"/usr/bin/env PYTHONPATH={python_path} ")
               for line in python_commands)


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


@pytest.mark.skipif(os.name != "posix", reason="Linux activation storage trust")
@pytest.mark.parametrize("unsafe_parent", ["writable", "symlink"])
def test_activation_rejects_untrusted_deployment_parent_before_lease(
    tmp_path, unsafe_parent,
):
    actual_parent = tmp_path / "actual-opt"
    root = actual_parent / "hope-operations"
    root.mkdir(parents=True)
    if unsafe_parent == "writable":
        actual_parent.chmod(0o777)
        observed_root = root
    else:
        linked_parent = tmp_path / "opt"
        linked_parent.symlink_to(actual_parent, target_is_directory=True)
        observed_root = linked_parent / root.name

    with pytest.raises(
        module.DeploymentError, match="^OPERATIONS_ACTIVATION_ROOT_UNTRUSTED$",
    ):
        with module.activation_lock(
            observed_root, expected_current="1" * 40, target_sha="2" * 40,
        ):
            pytest.fail("untrusted deployment storage admitted activation")
    assert not (root / ".activation.lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="Linux activation storage trust")
@pytest.mark.parametrize("owner_scope", ["parent", "root"])
def test_activation_rejects_non_operator_owned_deployment_storage(
    tmp_path, monkeypatch, owner_scope,
):
    root = tmp_path / "hope-operations"
    root.mkdir()
    original_lstat = Path.lstat
    original_fstat = module.os.fstat

    def lstat_with_untrusted_parent(path):
        info = original_lstat(path)
        if owner_scope == "parent" and path == root.parent:
            return SimpleNamespace(
                st_mode=info.st_mode, st_uid=os.geteuid() + 1,
                st_dev=info.st_dev, st_ino=info.st_ino,
            )
        return info

    def fstat_with_untrusted_root(descriptor):
        info = original_fstat(descriptor)
        if owner_scope == "root" and module.stat.S_ISDIR(info.st_mode):
            return SimpleNamespace(
                st_mode=info.st_mode, st_uid=os.geteuid() + 1,
                st_dev=info.st_dev, st_ino=info.st_ino,
            )
        return info

    monkeypatch.setattr(Path, "lstat", lstat_with_untrusted_parent)
    monkeypatch.setattr(module.os, "fstat", fstat_with_untrusted_root)
    with pytest.raises(
        module.DeploymentError, match="^OPERATIONS_ACTIVATION_ROOT_UNTRUSTED$",
    ):
        with module.activation_lock(
            root, expected_current="1" * 40, target_sha="2" * 40,
        ):
            pytest.fail("non-operator-owned storage admitted activation")
    assert not (root / ".activation.lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="Linux directory durability; exercised in Linux CI")
def test_activation_lease_directory_entry_is_durable_before_work_and_after_cleanup(tmp_path, monkeypatch):
    events = []
    original_fsync = module.os.fsync

    def observe_fsync(descriptor):
        mode = module.os.fstat(descriptor).st_mode
        events.append("directory" if module.stat.S_ISDIR(mode) else "file")
        original_fsync(descriptor)

    monkeypatch.setattr(module.os, "fsync", observe_fsync)
    with module.activation_lock(tmp_path, expected_current="1" * 40, target_sha="2" * 40):
        assert events == ["file", "directory"]
        assert (tmp_path / ".activation.lock").is_file()
    assert events == ["file", "directory", "directory"]
    assert not (tmp_path / ".activation.lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="Linux directory durability; exercised in Linux CI")
def test_activation_never_starts_when_lease_directory_sync_fails(tmp_path, monkeypatch):
    original_fsync = module.os.fsync
    entered = []

    def fail_directory_sync(descriptor):
        if module.stat.S_ISDIR(module.os.fstat(descriptor).st_mode):
            raise OSError("host-specific sync detail")
        original_fsync(descriptor)

    monkeypatch.setattr(module.os, "fsync", fail_directory_sync)
    with pytest.raises(module.DeploymentError, match="^OPERATIONS_ACTIVATION_LEASE_DURABILITY_FAILED$"):
        with module.activation_lock(tmp_path, expected_current="1" * 40, target_sha="2" * 40):
            entered.append(True)
    assert entered == []
    assert not (tmp_path / ".activation.lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="Linux inode identity; exercised in Linux CI")
@pytest.mark.parametrize("change", ["missing", "replacement", "hardlink"])
def test_activation_cleanup_never_deletes_changed_lease_path(tmp_path, change):
    lease = tmp_path / ".activation.lock"
    alias = tmp_path / "lease-alias"
    replacement = b"independent recovery evidence"

    with pytest.raises(module.DeploymentError, match="^OPERATIONS_ACTIVATION_LEASE_CHANGED$"):
        with module.activation_lock(tmp_path, expected_current="1" * 40, target_sha="2" * 40):
            original = lease.read_bytes()
            if change == "hardlink":
                os.link(lease, alias)
            else:
                lease.unlink()
            if change == "replacement":
                lease.write_bytes(replacement)
                lease.chmod(0o600)

    if change == "missing":
        assert not lease.exists()
    elif change == "replacement":
        assert lease.read_bytes() == replacement
    else:
        assert lease.read_bytes() == alias.read_bytes() == original


@pytest.mark.skipif(os.name != "posix", reason="Linux inode identity; exercised in Linux CI")
def test_activation_setup_failure_never_deletes_unverified_replacement(tmp_path, monkeypatch):
    lease = tmp_path / ".activation.lock"
    replacement = b"independent recovery evidence"
    original_fstat = module.os.fstat
    failed = False

    def fail_created_lease_identity(descriptor):
        nonlocal failed
        info = original_fstat(descriptor)
        if module.stat.S_ISREG(info.st_mode) and lease.exists() and not failed:
            failed = True
            lease.unlink()
            lease.write_bytes(replacement)
            lease.chmod(0o600)
            raise OSError("host-specific identity failure")
        return info

    monkeypatch.setattr(module.os, "fstat", fail_created_lease_identity)
    with pytest.raises(module.DeploymentError, match="^OPERATIONS_ACTIVATION_LEASE_UNTRUSTED$"):
        with module.activation_lock(tmp_path, expected_current="1" * 40, target_sha="2" * 40):
            pytest.fail("unverified lease must never admit activation")
    assert lease.read_bytes() == replacement


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
    lease_uid = (tmp_path / ".activation.lock").stat().st_uid
    monkeypatch.setattr(module.os, "geteuid", lambda: lease_uid)
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


@pytest.mark.skipif(os.name != "posix", reason="Linux no-follow descriptor binding; exercised in Linux CI")
def test_recovery_snapshot_never_reads_replacement_deployed_metadata(tmp_path, monkeypatch):
    old, new, _, proc = _interrupted_activation(tmp_path, monkeypatch)
    deployed = tmp_path / "deployed.env"
    replacement = tmp_path / "replacement-deployed.env"
    replacement.write_text(f"HOPE_DEPLOYED_COMMIT={new}\n")
    original_is_symlink = Path.is_symlink
    swapped = False

    def swap_after_symlink_check(path):
        nonlocal swapped
        result = original_is_symlink(path)
        if path == deployed and not swapped:
            swapped = True
            deployed.unlink()
            os.symlink(replacement, deployed)
        return result

    monkeypatch.setattr(Path, "is_symlink", swap_after_symlink_check)
    result = module.inspect_activation_recovery(tmp_path, proc_root=proc)
    assert result["deployed_metadata"] == "EXPECTED"
    assert result["recovery_safe"] is False


@pytest.mark.skipif(os.name != "posix", reason="Linux trusted metadata inode; exercised in Linux CI")
def test_recovery_snapshot_rejects_multiply_linked_deployed_metadata(tmp_path, monkeypatch):
    _, _, _, proc = _interrupted_activation(tmp_path, monkeypatch)
    os.link(tmp_path / "deployed.env", tmp_path / "deployed-alias")
    result = module.inspect_activation_recovery(tmp_path, proc_root=proc)
    assert result["deployed_metadata"] == "OTHER"
    assert result["recovery_safe"] is False


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


@pytest.mark.skipif(os.name != "posix", reason="Linux no-follow descriptor binding; exercised in Linux CI")
def test_recovery_inspection_never_reads_replacement_path_after_validation(tmp_path, monkeypatch):
    """A pathname swap after metadata validation must not authenticate another file."""
    lease = tmp_path / ".activation.lock"
    lease.write_text("{}")
    replacement = tmp_path / "replacement-record"
    replacement.write_text(json.dumps({
        "schema_version": 1,
        "pid": 1,
        "boot_id": "11111111-1111-1111-1111-111111111111",
        "process_start_ticks": "1",
        "expected_current": "1" * 40,
        "target_sha": "2" * 40,
    }))
    original_lstat = Path.lstat
    swapped = False

    def swap_after_lstat(path):
        nonlocal swapped
        result = original_lstat(path)
        if path == lease and not swapped:
            swapped = True
            lease.unlink()
            os.symlink(replacement, lease)
        return result

    monkeypatch.setattr(Path, "lstat", swap_after_lstat)
    with pytest.raises(module.DeploymentError, match="^OPERATIONS_ACTIVATION_LOCK_INVALID$"):
        module.inspect_activation_recovery(tmp_path)
    assert replacement.is_file() and replacement.read_text().startswith("{")


def test_safe_stale_recovery_stops_services_and_archives_unchanged_lock(tmp_path, monkeypatch):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    (proc / str(pid) / "stat").unlink()
    before_pointer = os.readlink(tmp_path / "current")
    before_metadata = (tmp_path / "deployed.env").read_bytes()
    before_lease = (tmp_path / ".activation.lock").read_bytes()
    calls = []
    result = module.recover_stale_activation(
        tmp_path, expected_current=old, target_sha=new,
        quiesce=lambda: calls.append("stopped"), proc_root=proc,
    )
    assert result == {"schema_version": 1, "state": "RECOVERED", "services": "STOPPED",
                      "expected_current": old, "target_sha": new}
    assert calls == ["stopped"] and not (tmp_path / ".activation.lock").exists()
    archives = list(tmp_path.glob(".activation.recovered-*"))
    assert len(archives) == 1
    assert (archives[0] / "lease.json").read_bytes() == before_lease
    assert os.readlink(tmp_path / "current") == before_pointer
    assert (tmp_path / "deployed.env").read_bytes() == before_metadata


@pytest.mark.skipif(os.name != "posix", reason="Linux descriptor-bound stale recovery")
def test_stale_recovery_rejects_root_replaced_during_quiesce(tmp_path, monkeypatch):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    (proc / str(pid) / "stat").unlink()
    displaced = tmp_path.parent / f"{tmp_path.name}-displaced"

    def replace_root_and_move_same_lease():
        tmp_path.rename(displaced)
        tmp_path.mkdir()
        releases = tmp_path / "releases"
        (releases / old).mkdir(parents=True)
        (releases / new).mkdir()
        os.symlink(releases / old, tmp_path / "current", target_is_directory=True)
        (tmp_path / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={old}\n")
        os.replace(displaced / ".activation.lock", tmp_path / ".activation.lock")

    with pytest.raises(module.DeploymentError, match="ACTIVATION_ROOT_CHANGED"):
        module.recover_stale_activation(
            tmp_path, expected_current=old, target_sha=new,
            quiesce=replace_root_and_move_same_lease, proc_root=proc,
        )
    assert (tmp_path / ".activation.lock").is_file()
    assert not list(tmp_path.glob(".activation.recovered-*"))


@pytest.mark.skipif(os.name != "posix", reason="Linux descriptor-bound stale recovery")
def test_stale_recovery_rechecks_state_on_held_root_not_transient_replacement(
    tmp_path, monkeypatch,
):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    (proc / str(pid) / "stat").unlink()
    displaced = tmp_path.parent / f"{tmp_path.name}-displaced-during-recheck"
    original_inspect = module.inspect_activation_recovery
    inspections = 0

    def inspect_with_transient_safe_replacement(root, **kwargs):
        nonlocal inspections
        inspections += 1
        if inspections == 1:
            return original_inspect(root, **kwargs)
        tmp_path.rename(displaced)
        tmp_path.mkdir()
        releases = tmp_path / "releases"
        (releases / old).mkdir(parents=True)
        (releases / new).mkdir()
        os.symlink(releases / old, tmp_path / "current", target_is_directory=True)
        (tmp_path / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={old}\n")
        os.replace(displaced / ".activation.lock", tmp_path / ".activation.lock")
        try:
            return original_inspect(root, **kwargs)
        finally:
            os.replace(tmp_path / ".activation.lock", displaced / ".activation.lock")
            (tmp_path / "current").unlink()
            (tmp_path / "deployed.env").unlink()
            (releases / old).rmdir()
            (releases / new).rmdir()
            releases.rmdir()
            tmp_path.rmdir()
            displaced.rename(tmp_path)

    monkeypatch.setattr(module, "inspect_activation_recovery", inspect_with_transient_safe_replacement)

    def make_held_root_unsafe():
        (tmp_path / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={new}\n")

    with pytest.raises(module.DeploymentError, match="ACTIVATION_STATE_CHANGED"):
        module.recover_stale_activation(
            tmp_path, expected_current=old, target_sha=new,
            quiesce=make_held_root_unsafe, proc_root=proc,
        )
    assert (tmp_path / ".activation.lock").is_file()
    assert (tmp_path / "deployed.env").read_text() == f"HOPE_DEPLOYED_COMMIT={new}\n"


@pytest.mark.skipif(os.name != "posix", reason="Linux atomic recovery quarantine; exercised in Linux CI")
def test_stale_recovery_preserves_replacement_raced_at_quarantine(tmp_path, monkeypatch):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    (proc / str(pid) / "stat").unlink()
    lease = tmp_path / ".activation.lock"
    replacement = tmp_path / "independent-recovery-evidence"
    replacement_payload = json.dumps({
        "schema_version": 1, "pid": pid, "boot_id": "f" * 32,
        "process_start_ticks": "999", "expected_current": old,
        "target_sha": new,
    }, sort_keys=True).encode()
    replacement.write_bytes(replacement_payload)
    replacement.chmod(0o600)
    original_replace, original_rename = os.replace, os.rename
    swapped = False

    def install_replacement():
        nonlocal swapped
        if not swapped:
            swapped = True
            lease.unlink()
            original_replace(replacement, lease)

    def swap_before_quarantine(source, destination, *args, **kwargs):
        if Path(source) == lease:
            install_replacement()
        return original_replace(source, destination)

    def swap_before_descriptor_quarantine(source, destination, *args, **kwargs):
        if source == ".activation.lock":
            install_replacement()
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(module.os, "replace", swap_before_quarantine)
    monkeypatch.setattr(module.os, "rename", swap_before_descriptor_quarantine)
    with pytest.raises(module.DeploymentError, match="^OPERATIONS_ACTIVATION_STATE_CHANGED$"):
        module.recover_stale_activation(
            tmp_path, expected_current=old, target_sha=new,
            quiesce=lambda: None, proc_root=proc,
        )
    assert not lease.exists()
    archives = list(tmp_path.glob(".activation.recovered-*"))
    assert len(archives) == 1
    assert (archives[0] / "lease.json").read_bytes() == replacement_payload


@pytest.mark.skipif(os.name != "posix", reason="Linux descriptor-bound recovery archive")
def test_stale_recovery_never_quarantines_through_replaced_root_path(tmp_path, monkeypatch):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    (proc / str(pid) / "stat").unlink()
    displaced = tmp_path.parent / f"{tmp_path.name}-displaced-at-archive"
    original_mkdir = os.mkdir
    attacked = False

    def replace_root_before_archive(path, mode=0o777, *, dir_fd=None):
        nonlocal attacked
        if not attacked and Path(path).name.startswith(".activation.recovered-"):
            attacked = True
            tmp_path.rename(displaced)
            tmp_path.mkdir()
            releases = tmp_path / "releases"
            (releases / old).mkdir(parents=True)
            (releases / new).mkdir()
            os.symlink(releases / old, tmp_path / "current", target_is_directory=True)
            (tmp_path / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={old}\n")
            os.replace(displaced / ".activation.lock", tmp_path / ".activation.lock")
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "mkdir", replace_root_before_archive)
    with pytest.raises(module.DeploymentError, match="ACTIVATION_(STATE|ROOT)_CHANGED"):
        module.recover_stale_activation(
            tmp_path, expected_current=old, target_sha=new,
            quiesce=lambda: None, proc_root=proc,
        )
    assert attacked is True
    assert (tmp_path / ".activation.lock").is_file()
    assert not list(tmp_path.glob(".activation.recovered-*"))


@pytest.mark.skipif(os.name != "posix", reason="Linux directory descriptor binding; exercised in Linux CI")
def test_stale_recovery_never_moves_lease_through_replaced_archive_path(tmp_path, monkeypatch):
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    (proc / str(pid) / "stat").unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_lease = outside / "lease.json"
    outside_payload = b"independent evidence\n"
    outside_lease.write_bytes(outside_payload)
    original_replace, original_rename = os.replace, os.rename
    attacked = False

    def replace_archive_path():
        nonlocal attacked
        if attacked:
            return
        attacked = True
        archive, = tmp_path.glob(".activation.recovered-*")
        archive.rmdir()
        os.symlink(outside, archive, target_is_directory=True)

    def attacked_replace(source, destination, *args, **kwargs):
        if Path(source) == tmp_path / ".activation.lock":
            replace_archive_path()
        return original_replace(source, destination, *args, **kwargs)

    def attacked_rename(source, destination, *args, **kwargs):
        if source == ".activation.lock" or Path(source) == tmp_path / ".activation.lock":
            replace_archive_path()
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(module.os, "replace", attacked_replace)
    monkeypatch.setattr(module.os, "rename", attacked_rename)
    with pytest.raises(module.DeploymentError, match="^OPERATIONS_ACTIVATION_STATE_CHANGED$"):
        module.recover_stale_activation(
            tmp_path, expected_current=old, target_sha=new,
            quiesce=lambda: None, proc_root=proc,
        )
    assert attacked is True
    assert outside_lease.read_bytes() == outside_payload


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
