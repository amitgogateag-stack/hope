"""Exporter rollback is pair-atomic for known conflicts and reconciles systemd.

All tests use a private temporary directory and simulated systemd metadata.
No host service manager is contacted.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[1] / "deploy/operations/install_exporter_units.py"
spec = importlib.util.spec_from_file_location("exporter_rollback_consistency", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ABSENT = dict(LoadState="not-found", ActiveState="inactive",
              UnitFileState="", FragmentPath="", DropInPaths="")


def installed(name, directory):
    return dict(LoadState="loaded", ActiveState="inactive",
                UnitFileState="static" if name.endswith(".service") else "disabled",
                FragmentPath=str(directory / name), DropInPaths="")


@pytest.fixture
def pair(tmp_path):
    payloads = {name: ("reviewed:" + name).encode() for name in mod.NAMES}
    entries = []
    for name, data in payloads.items():
        path = tmp_path / name
        path.write_bytes(data)
        info = path.stat()
        entries.append((name, (info.st_dev, info.st_ino, info.st_size,
                               info.st_mtime_ns, info.st_ctime_ns),
                        hashlib.sha256(data).hexdigest()))
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    yield tmp_path, fd, entries, payloads
    os.close(fd)


def observer(directory, *, unsafe_name=None, stale=False):
    def state(name):
        value = installed(name, directory) if (directory / name).exists() or stale else dict(ABSENT)
        if name == unsafe_name:
            value = dict(value, ActiveState="active")
        return value
    return SimpleNamespace(
        ABSENT_UNIT=ABSENT, unit_metadata=state,
        _read_regular_at=lambda fd, name, *, owner_uid: (directory / name).read_bytes(),
    )


def rollback(pair, inspector, reload):
    directory, fd, entries, payloads = pair
    mod._rollback(fd, entries, payloads, inspector, owner_uid=os.getuid(),
                  unit_dir=directory, daemon_reload=reload)


@pytest.mark.parametrize("unsafe_name", mod.NAMES)
def test_known_unsafe_peer_preserves_both_units_without_reload(pair, unsafe_name):
    directory, _, _, _ = pair
    reloads = []
    with pytest.raises(mod.InstallError, match="^EXPORTER_ROLLBACK_UNVERIFIED$"):
        rollback(pair, observer(directory, unsafe_name=unsafe_name),
                 lambda: reloads.append("reload"))
    assert all((directory / name).is_file() for name in mod.NAMES)
    assert reloads == []


def test_verified_pair_is_removed_reloaded_and_absence_verified(pair):
    directory, _, _, _ = pair
    reloads = []
    rollback(pair, observer(directory), lambda: reloads.append("reload"))
    assert reloads == ["reload"]
    assert all(not (directory / name).exists() for name in mod.NAMES)


def test_failed_reconciliation_is_not_reported_as_success(pair):
    directory, _, _, _ = pair
    reloads = []
    with pytest.raises(mod.InstallError, match="^EXPORTER_ROLLBACK_UNVERIFIED$"):
        rollback(pair, observer(directory, stale=True),
                 lambda: reloads.append("reload"))
    assert reloads == ["reload"]


def test_daemon_reload_failure_is_reported_even_after_file_removal(pair):
    directory, _, _, _ = pair
    def failed_reload():
        raise RuntimeError("synthetic daemon-reload failure")
    with pytest.raises(mod.InstallError, match="^EXPORTER_ROLLBACK_UNVERIFIED$"):
        rollback(pair, observer(directory), failed_reload)
    assert all(not (directory / name).exists() for name in mod.NAMES)


def test_replacement_during_second_validation_is_preserved(pair):
    directory, _, _, _ = pair
    inspector = observer(directory)
    read = inspector._read_regular_at
    counts = {}
    def racing_read(fd, name, *, owner_uid):
        counts[name] = counts.get(name, 0) + 1
        if name == mod.NAMES[1] and counts[name] == 2:
            (directory / name).write_bytes(b"attacker replacement")
        return read(fd, name, owner_uid=owner_uid)
    inspector._read_regular_at = racing_read
    reloads = []
    with pytest.raises(mod.InstallError, match="^EXPORTER_ROLLBACK_UNVERIFIED$"):
        rollback(pair, inspector, lambda: reloads.append("reload"))
    assert (directory / mod.NAMES[1]).read_bytes() == b"attacker replacement"
    assert reloads == []
