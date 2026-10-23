"""Persistent kernel guard prevents lease-path replacement races."""
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hope.api import operations_deployment as module


@pytest.fixture
def guard_platform(monkeypatch):
    if os.name == "nt":
        # Exercise orchestration with a real Windows kernel byte-range lock.
        # Linux CI exercises the production flock implementation unchanged.
        import msvcrt
        def flock(fd, flags):
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError() from exc
        monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(flock=flock, LOCK_EX=1, LOCK_NB=2))
        monkeypatch.setattr(module.os, "O_NOFOLLOW", 0, raising=False)
        monkeypatch.setattr(module.os, "geteuid", lambda: 0, raising=False)
        original_stat = module.os.fstat
        def posix_metadata(fd):
            info = original_stat(fd)
            # Windows mode bits describe ACL-backed files as writable; model
            # the POSIX 0600 metadata separately from the real kernel lock.
            return SimpleNamespace(st_mode=0o100600, st_uid=0, st_nlink=info.st_nlink)
        monkeypatch.setattr(module.os, "fstat", posix_metadata)
    monkeypatch.setattr(module, "_process_identity", lambda *args: ("1" * 36, "10"))


def test_guard_excludes_independent_descriptors_and_reuses_same_inode(tmp_path, guard_platform):
    with module._activation_guard(tmp_path):
        inode = (tmp_path / ".activation.guard").stat().st_ino
        with pytest.raises(module.DeploymentError, match="ACTIVATION_LOCKED"):
            with module._activation_guard(tmp_path):
                pytest.fail("overlapping guard entered")
    with module._activation_guard(tmp_path):
        assert (tmp_path / ".activation.guard").stat().st_ino == inode
    assert (tmp_path / ".activation.guard").is_file()


@pytest.mark.skipif(os.name != "posix", reason="Production shared flock is exercised on Linux CI")
def test_shared_observations_overlap_but_exclude_mutation(tmp_path):
    with module._activation_guard(tmp_path, shared=True):
        with module._activation_guard(tmp_path, shared=True):
            with pytest.raises(module.DeploymentError, match="ACTIVATION_LOCKED"):
                with module._activation_guard(tmp_path):
                    pytest.fail("exclusive mutation entered during observation")
    with module._activation_guard(tmp_path):
        with pytest.raises(module.DeploymentError, match="ACTIVATION_LOCKED"):
            with module._activation_guard(tmp_path, shared=True):
                pytest.fail("observation entered during mutation")


@pytest.mark.skipif(os.name != "posix", reason="Production cross-privilege flock is Linux-only")
def test_shared_observation_never_opens_private_root_guard(tmp_path, monkeypatch):
    private = tmp_path / ".activation.guard"
    with module._activation_guard(tmp_path, shared=True):
        assert not private.exists()
    with module._activation_guard(tmp_path):
        pass
    original = module.os.open
    def open_without_private_guard(path, *args, **kwargs):
        if Path(path) == private:
            raise AssertionError("read-only service tried to open root-owned guard")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(module.os, "open", open_without_private_guard)
    with module._activation_guard(tmp_path, shared=True):
        assert private.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_guard_releases_on_every_exit_without_removing_inode(tmp_path, guard_platform, failure):
    with pytest.raises(failure):
        with module._activation_guard(tmp_path):
            raise failure()
    with module._activation_guard(tmp_path):
        assert (tmp_path / ".activation.guard").exists()


@pytest.mark.parametrize("operation", ["activation", "recovery"])
def test_all_lease_mutators_fail_before_work_when_guard_is_held(tmp_path, guard_platform, operation):
    with module._activation_guard(tmp_path):
        with pytest.raises(module.DeploymentError, match="ACTIVATION_LOCKED"):
            if operation == "activation":
                with module.activation_lock(tmp_path, expected_current="1"*40, target_sha="2"*40):
                    pytest.fail("activation entered")
            else:
                module.recover_stale_activation(tmp_path, expected_current="1"*40, target_sha="2"*40,
                    quiesce=lambda: pytest.fail("contending recovery stopped services"))
    assert not (tmp_path / ".activation.lock").exists()


def test_activation_holds_guard_for_complete_lease_lifetime(tmp_path, guard_platform):
    with module.activation_lock(tmp_path, expected_current="1"*40, target_sha="2"*40):
        assert (tmp_path / ".activation.lock").is_file()
        with pytest.raises(module.DeploymentError, match="ACTIVATION_LOCKED"):
            with module._activation_guard(tmp_path):
                pytest.fail("lease lifetime escaped guard")
    assert not (tmp_path / ".activation.lock").exists()
    assert (tmp_path / ".activation.guard").exists()


@pytest.mark.parametrize("changed", ["mode", "owner", "links", "type"])
def test_guard_rejects_untrusted_inode(tmp_path, guard_platform, monkeypatch, changed):
    original = module.os.fstat
    def untrusted(fd):
        info = original(fd)
        if stat.S_ISDIR(info.st_mode):
            return info
        return SimpleNamespace(st_mode=0o100622 if changed=="mode" else 0o040600 if changed=="type" else info.st_mode,
            st_uid=module.os.geteuid()+1 if changed=="owner" else info.st_uid,
            st_nlink=2 if changed=="links" else info.st_nlink)
    monkeypatch.setattr(module.os, "fstat", untrusted)
    with pytest.raises(module.DeploymentError, match="GUARD_UNTRUSTED"):
        with module._activation_guard(tmp_path):
            pytest.fail("untrusted inode accepted")


@pytest.mark.skipif(os.name != "posix", reason="Linux no-follow/hard-link metadata; exercised on Linux CI")
@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_guard_rejects_link_aliases_without_changing_target(tmp_path, link):
    target = tmp_path / "untouched"
    target.write_bytes(b"must remain unchanged")
    target.chmod(0o600)
    path = tmp_path / ".activation.guard"
    if link == "symbolic":
        path.symlink_to(target)
    else:
        os.link(target, path)
    with pytest.raises((OSError, module.DeploymentError)):
        with module._activation_guard(tmp_path):
            pytest.fail("linked guard accepted")
    assert target.read_bytes() == b"must remain unchanged"


@pytest.mark.skipif(os.name != "posix", reason="Production Linux flock and process death; exercised on Linux CI")
def test_process_death_releases_kernel_guard_but_retains_authenticated_lease(tmp_path):
    script = """
from pathlib import Path
import sys,time
from hope.api.operations_deployment import activation_lock
with activation_lock(Path(sys.argv[1]), expected_current='1'*40, target_sha='2'*40):
    print('LOCKED', flush=True)
    time.sleep(30)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "LOCKED"
        with pytest.raises(module.DeploymentError, match="ACTIVATION_LOCKED"):
            with module._activation_guard(tmp_path):
                pytest.fail("process guard was not exclusive")
    finally:
        process.kill()
        process.communicate(timeout=5)
    with module._activation_guard(tmp_path):
        assert (tmp_path / ".activation.lock").is_file()
    with pytest.raises(module.DeploymentError, match="ACTIVATION_LOCKED"):
        with module.activation_lock(tmp_path, expected_current="1"*40, target_sha="2"*40):
            pytest.fail("crash lease was silently removed")


@pytest.mark.skipif(os.name != "posix", reason="Linux recovery fsync and symlinks; exercised on Linux CI")
def test_recovery_holds_guard_through_quiesce_and_final_unlink(tmp_path, monkeypatch):
    from test_operations_deployment import _interrupted_activation
    old, new, pid, proc = _interrupted_activation(tmp_path, monkeypatch)
    owner = (tmp_path / ".activation.lock").stat().st_uid
    monkeypatch.setattr(module.os, "geteuid", lambda: owner)
    (proc / str(pid) / "stat").unlink()
    lease = tmp_path / ".activation.lock"
    original = lease.read_bytes()
    attempted = []
    def contenders():
        for operation in ("activation", "recovery"):
            with pytest.raises(module.DeploymentError, match="ACTIVATION_LOCKED"):
                if operation == "activation":
                    with module.activation_lock(tmp_path, expected_current=old, target_sha=new):
                        pytest.fail("new activation entered during recovery")
                else:
                    module.recover_stale_activation(tmp_path, expected_current=old, target_sha=new,
                        quiesce=lambda: pytest.fail("second recovery entered"), proc_root=proc)
            attempted.append(operation)
            assert lease.read_bytes() == original
    result = module.recover_stale_activation(tmp_path, expected_current=old, target_sha=new,
        quiesce=contenders, proc_root=proc)
    assert result["state"] == "RECOVERED"
    assert attempted == ["activation", "recovery"]
    assert not lease.exists()
    with module.activation_lock(tmp_path, expected_current=old, target_sha=new):
        assert lease.is_file()
