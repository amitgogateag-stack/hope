"""Guarded dashboard-only release pointer and interrupted-operation recovery."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable

from hope.api.operations_deployment import DeploymentError, _process_identity, _sha, inspect_release, _activation_guard


POINTER = "dashboard-current"
METADATA = "dashboard-deployed.env"
PENDING = ".dashboard-current.next"
LOCK = ".dashboard-activation.lock"
GUARD = ".dashboard-activation.guard"


def _write_metadata(root: Path, sha: str) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".dashboard-deployed.", dir=root)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, f"HOPE_DASHBOARD_DEPLOYED_COMMIT={sha}\n".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, root / METADATA)
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _point(root: Path, release: Path) -> None:
    temporary = root / PENDING
    if temporary.exists() or temporary.is_symlink():
        raise DeploymentError("DASHBOARD_SWITCH_ALREADY_PENDING")
    os.symlink(release, temporary, target_is_directory=True)
    os.replace(temporary, root / POINTER)
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@contextmanager
def _guard(root: Path):
    # Historical recovery shares the full/dashboard journal serialization fence.
    with _activation_guard(root):
        if (root/"dashboard-activation.json").exists() or (root/"dashboard-activation.json").is_symlink():
            raise DeploymentError("DASHBOARD_JOURNAL_WORKFLOW_REQUIRED")
        with _legacy_guard(root):yield


@contextmanager
def _legacy_guard(root: Path):
    try:
        import fcntl
    except ImportError as exc:
        raise DeploymentError("DASHBOARD_ACTIVATION_POSIX_REQUIRED") from exc
    path = root.resolve(strict=True) / GUARD
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022 or info.st_nlink != 1):
            raise DeploymentError("DASHBOARD_ACTIVATION_GUARD_UNTRUSTED")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentError("DASHBOARD_ACTIVATION_LOCKED") from exc
        yield
    finally:
        os.close(descriptor)


@contextmanager
def dashboard_activation_lock(root: Path, *, expected_current: str, target_sha: str):
    expected_current, target_sha = _sha(expected_current), _sha(target_sha)
    with _guard(root):
        path = root.resolve(strict=True) / LOCK
        boot_id, started = _process_identity(os.getpid())
        record = json.dumps({
            "schema_version": 1, "pid": os.getpid(), "boot_id": boot_id,
            "process_start_ticks": started, "expected_current": expected_current,
            "target_sha": target_sha,
        }, sort_keys=True, separators=(",", ":")).encode("ascii")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError as exc:
            raise DeploymentError("DASHBOARD_ACTIVATION_LOCKED") from exc
        try:
            os.write(descriptor, record)
            os.fsync(descriptor)
            yield
        finally:
            os.close(descriptor)
            path.unlink()


def _read_lock(root: Path, proc_root: Path) -> tuple[dict, str]:
    path = root / LOCK
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022 or info.st_size > 4096):
        raise DeploymentError("DASHBOARD_ACTIVATION_LOCK_UNTRUSTED")
    try:
        record = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("DASHBOARD_ACTIVATION_LOCK_INVALID") from exc
    fields = {"schema_version", "pid", "boot_id", "process_start_ticks", "expected_current", "target_sha"}
    if (not isinstance(record, dict) or set(record) != fields or record["schema_version"] != 1
            or type(record["pid"]) is not int or record["pid"] <= 0
            or not isinstance(record["boot_id"], str)
            or not isinstance(record["process_start_ticks"], str)
            or not record["process_start_ticks"].isdigit()):
        raise DeploymentError("DASHBOARD_ACTIVATION_LOCK_INVALID")
    record["expected_current"] = _sha(record["expected_current"])
    record["target_sha"] = _sha(record["target_sha"])
    try:
        identity = _process_identity(record["pid"], proc_root)
        state = "ACTIVE" if identity == (record["boot_id"], record["process_start_ticks"]) else "STALE"
    except FileNotFoundError:
        state = "STALE"
    except OSError:
        state = "UNKNOWN"
    return record, state


def inspect_dashboard_recovery(root: Path, *, proc_root: Path = Path("/proc")) -> dict[str, object]:
    root = root.resolve(strict=True)
    if not (root / LOCK).exists() and not (root / LOCK).is_symlink():
        return {"schema_version": 1, "state": "ABSENT", "recovery_safe": True}
    record, state = _read_lock(root, proc_root)
    expected, target = record["expected_current"], record["target_sha"]
    pointer = root / POINTER
    try:
        resolved = pointer.resolve(strict=True) if pointer.is_symlink() else None
    except OSError:
        resolved = None
    releases = root / "releases"
    pointer_state = "EXPECTED" if resolved == releases / expected else "TARGET" if resolved == releases / target else "OTHER"
    try:
        content = (root / METADATA).read_text()
    except OSError:
        content = ""
    metadata = ("EXPECTED" if content == f"HOPE_DASHBOARD_DEPLOYED_COMMIT={expected}\n" else
                "TARGET" if content == f"HOPE_DASHBOARD_DEPLOYED_COMMIT={target}\n" else "OTHER")
    pending_path = root / PENDING
    try:
        pending_resolved = pending_path.resolve(strict=True) if pending_path.is_symlink() else None
    except OSError:
        pending_resolved = None
    pending = ("ABSENT" if not pending_path.exists() and not pending_path.is_symlink() else
               "EXPECTED" if pending_resolved == releases / expected else
               "TARGET" if pending_resolved == releases / target else "OTHER")
    safe = (state == "STALE" and pointer_state in {"EXPECTED", "TARGET"}
            and metadata in {"EXPECTED", "TARGET"} and pending in {"ABSENT", "EXPECTED", "TARGET"})
    return {"schema_version": 1, "state": state, "expected_current": expected,
            "target_sha": target, "dashboard_pointer": pointer_state,
            "dashboard_metadata": metadata, "pending_pointer": pending,
            "recovery_safe": safe}


def _fingerprint(root: Path) -> tuple[int, int, int, int, str]:
    path = root / LOCK
    info, content = path.lstat(), path.read_bytes()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, hashlib.sha256(content).hexdigest()


def recover_dashboard_activation(root: Path, *, expected_current: str, target_sha: str,
                                 quiesce: Callable[[], None], proc_root: Path = Path("/proc")) -> dict[str, object]:
    expected_current, target_sha = _sha(expected_current), _sha(target_sha)
    root = root.resolve(strict=True)
    with _guard(root):
        before = _fingerprint(root)
        diagnosis = inspect_dashboard_recovery(root, proc_root=proc_root)
        if (_fingerprint(root) != before or diagnosis.get("state") != "STALE"
                or diagnosis.get("recovery_safe") is not True
                or diagnosis.get("expected_current") != expected_current
                or diagnosis.get("target_sha") != target_sha):
            raise DeploymentError("DASHBOARD_ACTIVATION_RECOVERY_DENIED")
        quiesce()
        if inspect_dashboard_recovery(root, proc_root=proc_root) != diagnosis or _fingerprint(root) != before:
            raise DeploymentError("DASHBOARD_ACTIVATION_STATE_CHANGED")
        pending = root / PENDING
        if pending.is_symlink():
            pending.unlink()
        _point(root, root / "releases" / expected_current)
        _write_metadata(root, expected_current)
        (root / LOCK).unlink()
        return {"schema_version": 1, "state": "RECOVERED", "dashboard_service": "STOPPED",
                "expected_current": expected_current, "target_sha": target_sha}


def switch_dashboard_release(root: Path, target_sha: str, *, expected_current: str,
                             accept: Callable[[], None], before_switch: Callable[[], None] = lambda: None) -> None:
    target_sha, expected_current = _sha(target_sha), _sha(expected_current)
    root = root.resolve(strict=True)
    expected = (root / "releases" / expected_current).resolve(strict=True)
    target = (root / "releases" / target_sha).resolve(strict=True)
    if expected != root / "releases" / expected_current or target != root / "releases" / target_sha:
        raise DeploymentError("DASHBOARD_RELEASE_LEASE_MISMATCH")
    inspect_release(expected, expected_current)
    inspect_release(target, target_sha)
    with dashboard_activation_lock(root, expected_current=expected_current, target_sha=target_sha):
        pointer = root / POINTER
        if pointer.exists() or pointer.is_symlink():
            if not pointer.is_symlink() or pointer.resolve(strict=True) != expected:
                raise DeploymentError("DASHBOARD_RELEASE_LEASE_MISMATCH")
        else:
            _point(root, expected)
            _write_metadata(root, expected_current)
        before_switch()
        _point(root, target)
        _write_metadata(root, target_sha)
        try:
            accept()
        except BaseException:
            _point(root, expected)
            _write_metadata(root, expected_current)
            raise
