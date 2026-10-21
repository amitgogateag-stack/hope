#!/usr/bin/env python3
"""Install only the reviewed M3 exporter units; never start or enable them."""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path("/opt/hope-operations")
UNIT_DIR = Path("/etc/systemd/system")
NAMES = ("hope-ops-dashboard-export.service", "hope-ops-dashboard-export.timer")
COMMAND_ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "HOME": "/nonexistent",
}
AT_EMPTY_PATH = 0x1000


class InstallError(RuntimeError):
    """Fail-closed exporter installation error."""


def _load_inspector():
    path = Path(__file__).with_name("inspect_exporter_install.py")
    spec = importlib.util.spec_from_file_location("hope_exporter_inspector", path)
    if spec is None or spec.loader is None:
        raise InstallError("EXPORTER_INSPECTOR_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_journal():
    path = Path(__file__).with_name("exporter_install_journal.py")
    spec = importlib.util.spec_from_file_location("hope_exporter_journal", path)
    if spec is None or spec.loader is None:
        raise InstallError("EXPORTER_JOURNAL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise InstallError("EXPORTER_STAGE_WRITE_FAILED")
        view = view[written:]


def _absent(name: str, directory_fd: int) -> bool:
    if name not in NAMES:
        raise InstallError("EXPORTER_UNIT_NAME_UNVERIFIED")
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise InstallError("EXPORTER_UNIT_PATH_UNVERIFIED") from exc
    return False


def _named_identity(name: str, directory_fd: int) -> tuple[int, int]:
    value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):
        raise InstallError("EXPORTER_UNIT_PATH_UNVERIFIED")
    return value.st_dev, value.st_ino


def _unit_state(inspector, name: str) -> dict[str, str]:
    if name not in NAMES:
        raise InstallError("EXPORTER_UNIT_NAME_UNVERIFIED")
    try:
        return inspector.unit_metadata(name)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise InstallError("EXPORTER_UNIT_STATE_UNVERIFIED") from None


def _assert_units_absent(inspector) -> None:
    for name in NAMES:
        if _unit_state(inspector, name) != inspector.ABSENT_UNIT:
            raise InstallError("EXPORTER_UNIT_NOT_ABSENT:" + name)


def _expected_installed_state(name: str, unit_dir: Path) -> dict[str, str]:
    return {"LoadState": "loaded", "ActiveState": "inactive",
            "UnitFileState": "static" if name.endswith(".service") else "disabled",
            "FragmentPath": str(unit_dir / name), "DropInPaths": ""}


def _assert_installed_quiescent(inspector, unit_dir: Path) -> None:
    for name in NAMES:
        if _unit_state(inspector, name) != _expected_installed_state(name, unit_dir):
            raise InstallError("EXPORTER_INSTALLED_STATE_UNVERIFIED:" + name)


def _daemon_reload() -> None:
    subprocess.run(
        ["/usr/bin/systemctl", "daemon-reload"],
        check=True,
        timeout=90,
        env=COMMAND_ENVIRONMENT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _verify_rollback_entry(
    directory_fd: int,
    entry: tuple[str, tuple[int, int], str],
    payloads: dict[str, bytes],
    inspector,
    *,
    owner_uid: int,
    unit_dir: Path,
) -> bool:
    name, identity, digest = entry
    observed = _unit_state(inspector, name)
    if observed not in (inspector.ABSENT_UNIT, _expected_installed_state(name, unit_dir)):
        raise InstallError("EXPORTER_ROLLBACK_UNVERIFIED")
    try:
        current = inspector._read_regular_at(
            directory_fd, name, owner_uid=owner_uid
        )
    except FileNotFoundError:
        if observed == inspector.ABSENT_UNIT:
            return False
        raise InstallError("EXPORTER_ROLLBACK_UNVERIFIED") from None
    if (_named_identity(name, directory_fd) != identity
            or hashlib.sha256(current).hexdigest() != digest
            or current != payloads[name]):
        raise InstallError("EXPORTER_ROLLBACK_UNVERIFIED")
    return True


def _rollback(
    directory_fd: int,
    installed: list[tuple[str, tuple[int, int], str]],
    payloads: dict[str, bytes],
    inspector,
    *,
    owner_uid: int,
    unit_dir: Path = UNIT_DIR,
    daemon_reload=None,
) -> None:
    if not installed:
        return
    reload_units = daemon_reload or _daemon_reload
    # Validate the entire pair before deleting either unit. A known unsafe
    # peer must never cause a partial rollback of the verified peer.
    try:
        for entry in reversed(installed):
            _verify_rollback_entry(
                directory_fd, entry, payloads, inspector,
                owner_uid=owner_uid, unit_dir=unit_dir,
            )
    except (OSError, ValueError, InstallError, KeyError):
        raise InstallError("EXPORTER_ROLLBACK_UNVERIFIED") from None

    unsafe = False
    removed = False
    for entry in reversed(installed):
        try:
            # Recheck immediately before each unlink to reject replacements.
            present = _verify_rollback_entry(
                directory_fd, entry, payloads, inspector,
                owner_uid=owner_uid, unit_dir=unit_dir,
            )
            if present:
                os.unlink(entry[0], dir_fd=directory_fd)
                removed = True
                os.fsync(directory_fd)
        except (OSError, ValueError, InstallError, KeyError):
            unsafe = True
            break

    if removed:
        try:
            # Filesystem rollback alone leaves systemd's cached unit state
            # stale. Reconcile it even if an intervening removal failed.
            reload_units()
            for name, _, _ in installed:
                if not _absent(name, directory_fd):
                    raise InstallError("EXPORTER_ROLLBACK_UNVERIFIED")
            _assert_units_absent(inspector)
        except Exception:
            unsafe = True
    if unsafe:
        raise InstallError("EXPORTER_ROLLBACK_UNVERIFIED")


def _open_unnamed_stage(directory_fd: int) -> int:
    """Create a temporary regular inode with no directory entry."""
    temporary_flag = getattr(os, "O_TMPFILE", 0)
    if not temporary_flag:
        raise InstallError("EXPORTER_UNNAMED_STAGE_UNAVAILABLE")
    try:
        fd = os.open(
            ".",
            os.O_WRONLY | os.O_CLOEXEC | temporary_flag,
            0o600,
            dir_fd=directory_fd,
        )
    except OSError:
        raise InstallError("EXPORTER_UNNAMED_STAGE_UNAVAILABLE") from None
    value = os.fstat(fd)
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 0:
        os.close(fd)
        raise InstallError("EXPORTER_UNNAMED_STAGE_UNVERIFIED")
    return fd


def _publish_unnamed(fd: int, directory_fd: int, name: str) -> None:
    """Atomically give an unnamed inode its reviewed final name."""
    if name not in NAMES:
        raise InstallError("EXPORTER_UNIT_NAME_UNVERIFIED")
    linkat = ctypes.CDLL(None, use_errno=True).linkat
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    if linkat(fd, b"", directory_fd, os.fsencode(name), AT_EMPTY_PATH) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise InstallError("EXPORTER_UNIT_ALREADY_EXISTS:" + name)
    raise InstallError("EXPORTER_UNIT_PUBLICATION_FAILED") from OSError(
        error, os.strerror(error)
    )


def install_exporter_units(
    expected: str,
    *,
    root: Path = ROOT,
    unit_dir: Path = UNIT_DIR,
    owner_uid: int = 0,
    daemon_reload=None,
    inspector=None,
) -> tuple[str, ...]:
    """Install two disabled exporter units from one verified shared release."""
    inspector = inspector or _load_inspector()
    inspector.ROOT = root
    inspector.verify_release(expected, owner_uid=owner_uid)
    _assert_units_absent(inspector)

    root_fd = inspector._open_directory(root, owner_uid=owner_uid)
    releases_fd = release_fd = deploy_fd = operations_fd = unit_fd = -1
    staged: list[int] = []
    installed: list[tuple[str, tuple[int, int], str]] = []
    payloads: dict[str, bytes] = {}
    journal = None
    if daemon_reload is None:
        daemon_reload = _daemon_reload
    try:
        releases_fd = inspector._open_directory_at(
            root_fd, "releases", owner_uid=owner_uid
        )
        release_fd = inspector._open_directory_at(
            releases_fd, expected, owner_uid=owner_uid
        )
        deploy_fd = inspector._open_directory_at(
            release_fd, "deploy", owner_uid=owner_uid
        )
        operations_fd = inspector._open_directory_at(
            deploy_fd, "operations", owner_uid=owner_uid
        )
        unit_fd = inspector._open_directory(unit_dir, owner_uid=owner_uid)
        inspector._verify_directory_path(unit_dir, unit_fd, owner_uid=owner_uid)

        for name in NAMES:
            if not _absent(name, unit_fd):
                raise InstallError("EXPORTER_UNIT_ALREADY_EXISTS:" + name)
        journal = _load_journal().InstallJournal(
            root_fd, root, expected, unit_dir, owner_uid=owner_uid
        )
        journal.start()

        for name in NAMES:
            if not _absent(name, unit_fd):
                raise InstallError("EXPORTER_UNIT_ALREADY_EXISTS:" + name)
            payload = inspector._read_regular_at(
                operations_fd, name, owner_uid=owner_uid
            )
            expected_digest = inspector.EXPECTED_TEMPLATE_HASHES[name]
            if hashlib.sha256(payload).hexdigest() != expected_digest:
                raise InstallError("EXPORTER_TEMPLATE_IDENTITY_MISMATCH")
            payloads[name] = payload
            fd = _open_unnamed_stage(unit_fd)
            staged.append(fd)
            _write_all(fd, payload)
            os.fchmod(fd, 0o644)
            os.fsync(fd)

        journal.record("STAGED", [])

        inspector._verify_directory_at(
            deploy_fd, "operations", operations_fd, owner_uid=owner_uid
        )
        inspector._verify_directory_path(unit_dir, unit_fd, owner_uid=owner_uid)
        for name in NAMES:
            if not _absent(name, unit_fd):
                raise InstallError("EXPORTER_UNIT_ALREADY_EXISTS:" + name)

        _assert_units_absent(inspector)
        for name, fd in zip(NAMES, tuple(staged), strict=True):
            # Record the unnamed inode before publication. If linkat() succeeds
            # and any following operation raises, rollback can still identify
            # and remove exactly the published inode.
            value = os.fstat(fd)
            identity = (value.st_dev, value.st_ino)
            digest = hashlib.sha256(payloads[name]).hexdigest()
            installed.append((name, identity, digest))
            journal.record("PUBLISHING", installed)
            # AT_EMPTY_PATH publishes directly from the open descriptor. There
            # is no staging pathname for a concurrent process to replace and
            # no cleanup pathname that could target an untrusted inode.
            _publish_unnamed(fd, unit_fd, name)
            staged.remove(fd)
            os.close(fd)
            os.fsync(unit_fd)
            inspector._verify_directory_path(
                unit_dir, unit_fd, owner_uid=owner_uid
            )

        journal.record("RELOAD_PENDING", installed)
        daemon_reload()
        _assert_installed_quiescent(inspector, unit_dir)
        journal.record("COMMITTED", installed)
        return NAMES
    except BaseException as exc:
        # Transactional cleanup must also run for Python-level termination
        # requests. KeyboardInterrupt and SystemExit do not inherit Exception;
        # without this boundary they could strand a published unit.
        cleanup_error = None
        if journal is not None and journal.active:
            try:
                journal.record("ROLLBACK_PENDING" if installed else "ABORTED", installed)
            except Exception:
                cleanup_error = InstallError("EXPORTER_JOURNAL_UNVERIFIED")
        for fd in tuple(staged):
            staged.remove(fd)
            try:
                # Closing the last descriptor drops an unpublished unnamed
                # inode; no directory entry exists to authenticate or unlink.
                os.close(fd)
            except OSError:
                cleanup_error = InstallError(
                    "EXPORTER_STAGE_CLEANUP_UNVERIFIED"
                )
        if unit_fd >= 0 and installed:
            try:
                _rollback(
                    unit_fd, installed, payloads, inspector,
                    owner_uid=owner_uid, unit_dir=unit_dir,
                    daemon_reload=daemon_reload,
                )
            except InstallError as rollback_exc:
                if journal is not None and journal.active and journal.healthy:
                    try:
                        journal.record("ROLLBACK_UNVERIFIED", installed)
                    except Exception:
                        pass
                raise rollback_exc from exc
            if journal is not None and journal.active and journal.healthy:
                try:
                    journal.record("ROLLED_BACK", installed)
                except Exception:
                    cleanup_error = InstallError("EXPORTER_JOURNAL_UNVERIFIED")
        if cleanup_error is not None:
            raise cleanup_error from exc
        raise
    finally:
        if journal is not None:
            journal.close()
        for fd in tuple(staged):
            try:
                os.close(fd)
            except OSError:
                pass
        for fd in (unit_fd, operations_fd, deploy_fd, release_fd, releases_fd, root_fd):
            if fd >= 0:
                os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install reviewed M3 exporter units disabled"
    )
    parser.add_argument("--expected-shared", required=True)
    parser.add_argument("--approve-exporter-install", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0 or not args.approve_exporter_install:
        print("EXPORTER_INSTALL_EXPLICIT_ROOT_APPROVAL_REQUIRED")
        return 2
    try:
        installed = install_exporter_units(args.expected_shared)
    except Exception:
        print("EXPORTER_INSTALL_BLOCKED")
        return 2
    print("EXPORTER_UNITS_INSTALLED_DISABLED:" + ",".join(installed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
