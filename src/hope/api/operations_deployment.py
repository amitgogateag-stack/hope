"""Read-only EC2 acceptance checks and guarded release-pointer rollback.

No cloud API, migration, role grant or service activation is performed by this CLI.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
from pathlib import Path
from typing import Callable

from sqlalchemy import Connection, text


class DeploymentError(RuntimeError):
    pass


GIT_BINARY = "/usr/bin/git"
GIT_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}


def _sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DeploymentError("OPERATIONS_COMMIT_INVALID")
    return value


def _git(release: Path, *arguments: str) -> str:
    try:
        result = subprocess.run([GIT_BINARY, "--no-optional-locks",
                                 "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                                 "-c", "core.pager=cat", "-c", f"safe.directory={release}",
                                 "-C", str(release), *arguments],
                                check=True, capture_output=True, text=True, timeout=15, env=GIT_ENVIRONMENT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentError("OPERATIONS_RELEASE_GIT_UNAVAILABLE") from exc
    return result.stdout.strip()


def inspect_release(release: Path, expected_sha: str) -> dict[str, object]:
    expected_sha = _sha(expected_sha)
    release = release.resolve(strict=True)
    if Path(_git(release, "rev-parse", "--show-toplevel")).resolve() != release:
        raise DeploymentError("OPERATIONS_RELEASE_ROOT_INVALID")
    if _git(release, "remote", "get-url", "origin") != "https://github.com/amitgogateag-stack/hope.git":
        raise DeploymentError("OPERATIONS_RELEASE_ORIGIN_INVALID")
    if _git(release, "rev-parse", "HEAD") != expected_sha:
        raise DeploymentError("OPERATIONS_RELEASE_HEAD_MISMATCH")
    if _git(release, "status", "--porcelain", "--untracked-files=all"):
        raise DeploymentError("OPERATIONS_RELEASE_DIRTY")
    required = ("src/hope/api/paper_service.py", "src/hope/api/dashboard_server.py",
                "src/hope/api/dashboard_snapshot.py", "docs/dashboard/preview.html",
                "deploy/operations/hope-paper.service")
    if any(not (release / file).is_file() for file in required) or not tuple((release / "migrations").glob("*.sql")):
        raise DeploymentError("OPERATIONS_RELEASE_INCOMPLETE")
    return {"commit": expected_sha, "release": str(release), "status": "COMPATIBLE_CODE"}


def verify_database_role(connection: Connection, *, component: str) -> None:
    """Reject privileged roles and schema owners before service installation.

    Caller must begin a read-only transaction. No role names or URLs are returned.
    """
    if component not in {"paper", "dashboard"}:
        raise DeploymentError("OPERATIONS_COMPONENT_INVALID")
    if connection.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
        raise DeploymentError("OPERATIONS_PREFLIGHT_REQUIRES_READ_ONLY")
    privileged = connection.execute(text(
        "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls "
        "FROM pg_roles WHERE rolname=current_user"
    )).scalar_one()
    owns_schema = connection.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='public' "
        "AND pg_has_role(current_user,nspowner,'MEMBER')) OR "
        "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND pg_has_role(current_user,c.relowner,'MEMBER'))"
    )).scalar_one()
    creates_schema = connection.execute(text(
        "SELECT has_schema_privilege(current_user,'public','CREATE') OR "
        "has_database_privilege(current_user,current_database(),'CREATE')"
    )).scalar_one()
    if privileged or owns_schema or creates_schema:
        raise DeploymentError("OPERATIONS_DATABASE_ROLE_PRIVILEGED")
    if component == "dashboard":
        can_write = connection.execute(text(
            "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p') AND "
            "has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'))"
        )).scalar_one()
        if can_write:
            raise DeploymentError("OPERATIONS_DASHBOARD_ROLE_CAN_WRITE")


def _process_identity(pid: int, proc_root: Path = Path("/proc")) -> tuple[str, str]:
    """Return Linux boot and process-start identities; PID alone is not a lease."""
    boot_id = (proc_root / "sys/kernel/random/boot_id").read_text().strip()
    process = "self" if proc_root == Path("/proc") and pid == os.getpid() else str(pid)
    process_stat = (proc_root / process / "stat").read_text()
    closing = process_stat.rfind(")")
    fields = process_stat[closing + 2:].split() if closing >= 0 else []
    if not re.fullmatch(r"[0-9a-f-]{36}", boot_id) or len(fields) < 20 or not fields[19].isdigit():
        raise DeploymentError("OPERATIONS_PROCESS_IDENTITY_INVALID")
    return boot_id, fields[19]


def _trusted_activation_directory(
    info: os.stat_result, *, required_owner: int | None,
) -> bool:
    return (
        stat.S_ISDIR(info.st_mode)
        and not info.st_mode & 0o022
        and (required_owner is None or info.st_uid == required_owner)
    )


@contextmanager
def _activation_guard(root: Path, *, shared: bool = False):
    """Fence activation state across privileged and read-only processes.

    Fingerprints cannot make pathname unlink conditional. Both activation and
    stale recovery exclusively lock both the trusted deployment directory and
    the private, persistent guard inode across every lease operation. A
    read-only service only takes a shared lock on the directory it can already
    traverse, so it never needs access to the root-owned 0600 guard file.
    Process death releases both flocks; the authenticated lease survives.
    """
    try:
        import fcntl
    except ImportError as exc:
        raise DeploymentError("OPERATIONS_ACTIVATION_POSIX_REQUIRED") from exc
    requested_root = root
    parent_identity = None
    if os.name == "posix" and not shared:
        if not requested_root.is_absolute() or requested_root.parent == requested_root:
            raise DeploymentError("OPERATIONS_ACTIVATION_ROOT_UNTRUSTED")
        try:
            parent = requested_root.parent.lstat()
            root = requested_root.resolve(strict=True)
        except OSError as exc:
            raise DeploymentError("OPERATIONS_ACTIVATION_ROOT_UNTRUSTED") from exc
        if (root != requested_root
                or not _trusted_activation_directory(
                    parent, required_owner=os.geteuid(),
                )):
            raise DeploymentError("OPERATIONS_ACTIVATION_ROOT_UNTRUSTED")
        parent_identity = (parent.st_dev, parent.st_ino)
    else:
        root = requested_root.resolve(strict=True)
    directory_descriptor = None
    descriptor = None
    try:
        # The production contract is Linux. Retain the file-only emulation used
        # by Windows orchestration tests, which do not exercise shared flock.
        if os.name == "posix":
            directory_descriptor = os.open(
                root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            info = os.fstat(directory_descriptor)
            required_owner = None if shared else os.geteuid()
            if not _trusted_activation_directory(
                info, required_owner=required_owner,
            ):
                raise DeploymentError("OPERATIONS_ACTIVATION_ROOT_UNTRUSTED")
            if not shared:
                try:
                    named = root.lstat()
                    current_parent = root.parent.lstat()
                except OSError as exc:
                    raise DeploymentError(
                        "OPERATIONS_ACTIVATION_ROOT_CHANGED",
                    ) from exc
                if (not _trusted_activation_directory(
                        named, required_owner=required_owner,
                    ) or not _trusted_activation_directory(
                        current_parent, required_owner=required_owner,
                    ) or (named.st_dev, named.st_ino)
                        != (info.st_dev, info.st_ino)
                    or (current_parent.st_dev, current_parent.st_ino)
                        != parent_identity):
                    raise DeploymentError("OPERATIONS_ACTIVATION_ROOT_CHANGED")
            try:
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(directory_descriptor, mode | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DeploymentError("OPERATIONS_ACTIVATION_LOCKED") from exc
            if shared:
                yield
                return
        path = root / ".activation.guard"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022 or info.st_nlink != 1):
            raise DeploymentError("OPERATIONS_ACTIVATION_GUARD_UNTRUSTED")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentError("OPERATIONS_ACTIVATION_LOCKED") from exc
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)


@contextmanager
def activation_lock(root: Path, *, expected_current: str, target_sha: str):
    """Exclusive authenticated operation lease, retained after process death."""
    with _activation_guard(root):
        with _create_activation_lease(root, expected_current=expected_current, target_sha=target_sha):
            yield


def _sync_activation_directory(root: Path) -> None:
    """Make a lease pathname transition durable on the production Linux host."""
    if os.name != "posix":
        return
    descriptor = None
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        os.fsync(descriptor)
    except OSError as exc:
        raise DeploymentError("OPERATIONS_ACTIVATION_LEASE_DURABILITY_FAILED") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def _create_activation_lease(root: Path, *, expected_current: str, target_sha: str):
    expected_current, target_sha = _sha(expected_current), _sha(target_sha)
    root = root.resolve(strict=True)
    path = root / ".activation.lock"
    boot_id, started = _process_identity(os.getpid())
    record = json.dumps({
        "schema_version": 1, "pid": os.getpid(), "boot_id": boot_id,
        "process_start_ticks": started, "expected_current": expected_current,
        "target_sha": target_sha,
    }, sort_keys=True, separators=(",", ":")).encode("ascii")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise DeploymentError("OPERATIONS_ACTIVATION_LOCKED") from exc
    lease_identity = None
    try:
        if os.name == "posix":
            try:
                created = os.fstat(descriptor)
            except OSError as exc:
                raise DeploymentError("OPERATIONS_ACTIVATION_LEASE_UNTRUSTED") from exc
            lease_identity = (created.st_dev, created.st_ino)
            if (not stat.S_ISREG(created.st_mode) or created.st_uid != os.geteuid()
                    or created.st_mode & 0o022 or created.st_nlink != 1):
                raise DeploymentError("OPERATIONS_ACTIVATION_LEASE_UNTRUSTED")
        offset = 0
        while offset < len(record):
            written = os.write(descriptor, record[offset:])
            if written <= 0:
                raise DeploymentError("OPERATIONS_ACTIVATION_LEASE_DURABILITY_FAILED")
            offset += written
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise DeploymentError("OPERATIONS_ACTIVATION_LEASE_DURABILITY_FAILED") from exc
        # File fsync alone does not make the newly created directory entry
        # crash-durable. Recovery intent must survive before pointer work begins.
        _sync_activation_directory(root)
        yield
    finally:
        try:
            # If identity acquisition failed, the pathname cannot safely be
            # attributed to this descriptor. Preserve it for recovery review.
            if os.name != "posix" or lease_identity is not None:
                if lease_identity is not None:
                    named = path.lstat()
                    if (not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
                            or (named.st_dev, named.st_ino) != lease_identity):
                        raise DeploymentError("OPERATIONS_ACTIVATION_LEASE_CHANGED")
                path.unlink()
                # A clean operation must durably clear its lease as well; otherwise
                # a reboot can resurrect stale recovery evidence after success.
                _sync_activation_directory(root)
        except FileNotFoundError as exc:
            raise DeploymentError("OPERATIONS_ACTIVATION_LEASE_CHANGED") from exc
        finally:
            os.close(descriptor)


def _read_deployed_metadata(path: Path) -> str:
    """Read one small trusted metadata inode without reopening its pathname."""
    descriptor = None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_mode & 0o022 or before.st_nlink != 1
                or before.st_size > 128):
            return ""
        content = b""
        while len(content) <= 128:
            chunk = os.read(descriptor, 129 - len(content))
            if not chunk:
                break
            content += chunk
        after = os.fstat(descriptor)
        before_identity = (before.st_dev, before.st_ino, before.st_size,
                           before.st_mtime_ns, before.st_ctime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size,
                          after.st_mtime_ns, after.st_ctime_ns)
        if (before_identity != after_identity or after.st_nlink != 1
                or len(content) != after.st_size or len(content) > 128):
            return ""
        return content.decode("ascii")
    except (OSError, UnicodeError):
        return ""
    finally:
        if descriptor is not None:
            os.close(descriptor)


def inspect_activation_recovery(root: Path, *, proc_root: Path = Path("/proc")) -> dict[str, object]:
    """Diagnose an interrupted activation without removing locks or changing services."""
    root = root.resolve(strict=True)
    try:
        content, _ = _read_activation_lock(root)
    except FileNotFoundError:
        return {"schema_version": 1, "state": "ABSENT", "recovery_safe": True}
    try:
        record = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("OPERATIONS_ACTIVATION_LOCK_INVALID") from exc
    fields = {"schema_version", "pid", "boot_id", "process_start_ticks", "expected_current", "target_sha"}
    if (not isinstance(record, dict) or set(record) != fields or record["schema_version"] != 1
            or type(record["pid"]) is not int or record["pid"] <= 0
            or not isinstance(record["boot_id"], str)
            or not re.fullmatch(r"[0-9a-f-]{36}", record["boot_id"])
            or not isinstance(record["process_start_ticks"], str)
            or not record["process_start_ticks"].isdigit()):
        raise DeploymentError("OPERATIONS_ACTIVATION_LOCK_INVALID")
    expected, target = _sha(record["expected_current"]), _sha(record["target_sha"])
    try:
        live_boot, live_started = _process_identity(record["pid"], proc_root)
        lock_state = ("ACTIVE" if (live_boot, live_started) ==
                      (record["boot_id"], record["process_start_ticks"]) else "STALE")
    except FileNotFoundError:
        lock_state = "STALE"
    except OSError:
        lock_state = "UNKNOWN"

    releases = root / "releases"
    current = root / "current"
    try:
        resolved = current.resolve(strict=True) if current.is_symlink() else None
    except OSError:
        resolved = None
    pointer = "EXPECTED" if resolved == releases / expected else "TARGET" if resolved == releases / target else "OTHER"
    deployed = root / "deployed.env"
    content = _read_deployed_metadata(deployed)
    metadata = ("EXPECTED" if content == f"HOPE_DEPLOYED_COMMIT={expected}\n" else
                "TARGET" if content == f"HOPE_DEPLOYED_COMMIT={target}\n" else "OTHER")
    pending = "PRESENT" if (root / ".current.next").exists() or (root / ".current.next").is_symlink() else "ABSENT"
    safe = lock_state == "STALE" and pointer == metadata == "EXPECTED" and pending == "ABSENT"
    return {
        "schema_version": 1, "state": lock_state, "expected_current": expected,
        "target_sha": target, "current_pointer": pointer, "deployed_metadata": metadata,
        "pending_pointer": pending, "recovery_safe": safe,
    }


def _read_activation_record(
    path: Path | str, *, directory_descriptor: int | None = None,
) -> tuple[bytes, tuple[int, int, int, int, str]]:
    """Validate and read one bounded lease inode through a no-follow descriptor."""
    descriptor = None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags, dir_fd=directory_descriptor)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_mode & 0o022 or before.st_nlink != 1
                or before.st_size > 4096):
            raise DeploymentError("OPERATIONS_ACTIVATION_LOCK_UNTRUSTED")
        content = b""
        while len(content) <= 4096:
            chunk = os.read(descriptor, 4097 - len(content))
            if not chunk:
                break
            content += chunk
        after = os.fstat(descriptor)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if (before_identity != after_identity or after.st_nlink != 1
                or len(content) != after.st_size or len(content) > 4096):
            raise DeploymentError("OPERATIONS_ACTIVATION_LOCK_CHANGED")
        fingerprint = (*after_identity, hashlib.sha256(content).hexdigest())
        return content, fingerprint
    except FileNotFoundError:
        raise
    except DeploymentError:
        raise
    except OSError as exc:
        raise DeploymentError("OPERATIONS_ACTIVATION_LOCK_UNTRUSTED") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_activation_lock(root: Path) -> tuple[bytes, tuple[int, int, int, int, str]]:
    return _read_activation_record(root / ".activation.lock")


def _activation_lock_fingerprint(root: Path) -> tuple[int, int, int, int, str]:
    """Bind recovery to the exact regular-file lease that was inspected."""
    return _read_activation_lock(root)[1]


def _quarantine_activation_lock(
    root: Path, fingerprint: tuple[int, int, int, int, str],
) -> None:
    """Durably archive recovery evidence without conditionally unlinking a pathname."""
    archive = None
    for _ in range(8):
        candidate = root / f".activation.recovered-{secrets.token_hex(16)}"
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        archive = candidate
        break
    if archive is None:
        raise DeploymentError("OPERATIONS_ACTIVATION_ARCHIVE_UNAVAILABLE")

    # Persist the unique empty directory before moving the only lease name into
    # it. Linux binds the transition and subsequent read to open directory
    # descriptors so replacing the archive pathname cannot redirect the move.
    if os.name != "posix":
        _sync_activation_directory(root)
        destination = archive / "lease.json"
        try:
            os.replace(root / ".activation.lock", destination)
        except OSError as exc:
            raise DeploymentError("OPERATIONS_ACTIVATION_STATE_CHANGED") from exc
        moved_fingerprint = _read_activation_record(destination)[1]
        if moved_fingerprint != fingerprint:
            raise DeploymentError("OPERATIONS_ACTIVATION_STATE_CHANGED")
        return

    root_descriptor = archive_descriptor = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_descriptor = os.open(root, flags)
        archive_descriptor = os.open(archive, flags)
        created = os.fstat(archive_descriptor)
        archive_identity = (created.st_dev, created.st_ino)
        if (not stat.S_ISDIR(created.st_mode) or created.st_uid != os.geteuid()
                or stat.S_IMODE(created.st_mode) != 0o700):
            raise DeploymentError("OPERATIONS_ACTIVATION_ARCHIVE_UNTRUSTED")
        try:
            os.fsync(root_descriptor)
            os.rename(".activation.lock", "lease.json",
                      src_dir_fd=root_descriptor, dst_dir_fd=archive_descriptor)
            os.fsync(archive_descriptor)
            os.fsync(root_descriptor)
        except OSError as exc:
            raise DeploymentError("OPERATIONS_ACTIVATION_STATE_CHANGED") from exc
        moved_fingerprint = _read_activation_record(
            "lease.json", directory_descriptor=archive_descriptor,
        )[1]
        try:
            os.stat(".activation.lock", dir_fd=root_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DeploymentError("OPERATIONS_ACTIVATION_STATE_CHANGED")
        try:
            named_archive = archive.lstat()
        except FileNotFoundError as exc:
            raise DeploymentError("OPERATIONS_ACTIVATION_STATE_CHANGED") from exc
        if (not stat.S_ISDIR(named_archive.st_mode)
                or (named_archive.st_dev, named_archive.st_ino) != archive_identity
                or moved_fingerprint != fingerprint):
            raise DeploymentError("OPERATIONS_ACTIVATION_STATE_CHANGED")
    finally:
        if archive_descriptor is not None:
            os.close(archive_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)


def recover_stale_activation(
    root: Path, *, expected_current: str, target_sha: str,
    quiesce: Callable[[], None], proc_root: Path = Path("/proc"),
) -> dict[str, object]:
    """Remove only an authenticated, unchanged and safely stale activation lease.

    The caller must stop and verify all deployment services in ``quiesce``.  This
    operation never changes release pointers or metadata and never starts a unit.
    """
    with _activation_guard(root):
        return _recover_stale_activation(root, expected_current=expected_current,
            target_sha=target_sha, quiesce=quiesce, proc_root=proc_root)


def _recover_stale_activation(root: Path, *, expected_current: str, target_sha: str,
                            quiesce: Callable[[], None], proc_root: Path):
    expected_current, target_sha = _sha(expected_current), _sha(target_sha)
    root = root.resolve(strict=True)
    try:
        fingerprint = _activation_lock_fingerprint(root)
        diagnosis = inspect_activation_recovery(root, proc_root=proc_root)
        if _activation_lock_fingerprint(root) != fingerprint:
            raise DeploymentError("OPERATIONS_ACTIVATION_LOCK_CHANGED")
    except FileNotFoundError as exc:
        raise DeploymentError("OPERATIONS_ACTIVATION_LOCK_MISSING") from exc
    if (diagnosis.get("state") != "STALE" or diagnosis.get("recovery_safe") is not True
            or diagnosis.get("expected_current") != expected_current
            or diagnosis.get("target_sha") != target_sha):
        raise DeploymentError("OPERATIONS_ACTIVATION_RECOVERY_DENIED")

    quiesce()

    # Recheck every safety fact after stopping units. A concurrently changed
    # lease or release state leaves services stopped and requires review.
    try:
        repeated = inspect_activation_recovery(root, proc_root=proc_root)
        if repeated != diagnosis or _activation_lock_fingerprint(root) != fingerprint:
            raise DeploymentError("OPERATIONS_ACTIVATION_STATE_CHANGED")
        _quarantine_activation_lock(root, fingerprint)
    except FileNotFoundError as exc:
        raise DeploymentError("OPERATIONS_ACTIVATION_STATE_CHANGED") from exc
    return {"schema_version": 1, "state": "RECOVERED", "services": "STOPPED",
            "expected_current": expected_current, "target_sha": target_sha}


def switch_release(
    root: Path, target_sha: str, *, expected_current: str,
    accept: Callable[[], None], before_switch: Callable[[], None] = lambda: None,
) -> None:
    """Atomically switch a prepared release and restore pointer on failed acceptance.

    Services must be stopped by the operator first. This never rolls back data.
    The acceptance callback must raise on failed checks. CLI does not expose this
    mutation; the approved deployment wrapper invokes it only after preflight.
    """
    with activation_lock(root, expected_current=expected_current, target_sha=target_sha):
        _switch_release(root, target_sha, expected_current=expected_current,
                        accept=accept, before_switch=before_switch)


def _switch_release(root, target_sha, *, expected_current, accept, before_switch):
    target_sha, expected_current = _sha(target_sha), _sha(expected_current)
    root = root.resolve(strict=True)
    if any((root/name).exists() or (root/name).is_symlink()
           for name in ("dashboard-current","dashboard-activation.json",".dashboard-activation.lock",
                        ".dashboard-current.next","dashboard-deployed.env")):
        raise DeploymentError("OPERATIONS_DASHBOARD_OVERRIDE_REQUIRES_ROLLBACK")
    releases = (root / "releases").resolve(strict=True)
    current = root / "current"
    if not current.is_symlink():
        raise DeploymentError("OPERATIONS_CURRENT_MUST_BE_SYMLINK")
    previous = current.resolve(strict=True)
    target = (releases / target_sha).resolve(strict=True)
    if previous != releases / expected_current or target != releases / target_sha:
        raise DeploymentError("OPERATIONS_RELEASE_LEASE_MISMATCH")
    inspect_release(target, target_sha)
    temporary = root / ".current.next"
    if temporary.exists() or temporary.is_symlink():
        raise DeploymentError("OPERATIONS_SWITCH_ALREADY_PENDING")
    temporary_identity = None

    def point(path: Path) -> None:
        nonlocal temporary_identity
        temporary_identity = None
        os.symlink(path, temporary, target_is_directory=True)
        if os.name == "posix":
            created = temporary.lstat()
            if not stat.S_ISLNK(created.st_mode) or created.st_nlink != 1:
                raise DeploymentError("OPERATIONS_SWITCH_TEMP_UNTRUSTED")
            temporary_identity = (created.st_dev, created.st_ino, created.st_size,
                                  created.st_mtime_ns, created.st_ctime_ns, str(path))
        os.replace(temporary, current)
        # Acceptance may only observe a pointer whose rename is durable. This
        # also makes a failed-acceptance rollback durable before it is reported.
        _sync_activation_directory(root)

    try:
        before_switch()
        try:
            point(target)
        except BaseException:
            # Directory fsync can fail after the atomic rename changed the
            # visible pointer. Restore and sync the prior pointer before
            # exposing the failure; never run acceptance on an unsynced target.
            try:
                visible = current.resolve(strict=True)
            except OSError as exc:
                raise DeploymentError("OPERATIONS_RELEASE_POINTER_UNTRUSTED") from exc
            if visible == target:
                point(previous)
            elif visible != previous:
                raise DeploymentError("OPERATIONS_RELEASE_POINTER_UNTRUSTED")
            raise
        try:
            accept()
        except BaseException:
            point(previous)
            raise
    finally:
        # A failed rename can leave the temporary pathname behind. Never unlink
        # it by name: another process can replace that pathname between failure
        # and cleanup. Preserve both our pending pointer and any replacement for
        # explicit recovery review; successful atomic renames leave no entry.
        try:
            pending = temporary.lstat()
        except FileNotFoundError:
            pass
        else:
            if (temporary_identity is None or not stat.S_ISLNK(pending.st_mode)
                    or pending.st_nlink != 1
                    or (pending.st_dev, pending.st_ino, pending.st_size,
                        pending.st_mtime_ns, pending.st_ctime_ns,
                        os.readlink(temporary)) != temporary_identity):
                raise DeploymentError("OPERATIONS_SWITCH_TEMP_CHANGED")
            raise DeploymentError("OPERATIONS_SWITCH_TEMP_PRESERVED")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect prepared HOPE operations release without deployment")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--database-component", choices=("paper", "dashboard"))
    args = parser.parse_args()
    try:
        result = inspect_release(args.release, args.expected_sha)
        if args.database_component:
            from sqlalchemy import create_engine
            from hope.infrastructure.paper_service import verify_schema_compatibility
            url = os.environ.get("HOPE_DATABASE_URL")
            if not url:
                raise DeploymentError("OPERATIONS_DATABASE_NOT_CONFIGURED")
            engine = create_engine(url, connect_args={"connect_timeout": 10})
            try:
                with engine.connect() as connection:
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    connection.execute(text("SET LOCAL statement_timeout=15000"))
                    verify_database_role(connection, component=args.database_component)
                    verify_schema_compatibility(connection, args.release / "migrations")
            finally:
                engine.dispose()
            result["database"] = "COMPATIBLE_LEAST_PRIVILEGE"
        print(json.dumps(result, sort_keys=True))
    except Exception:
        # Neither driver exceptions nor subprocess output may disclose credentials.
        print(json.dumps({"status": "BLOCKED", "reason": "OPERATIONS_PREFLIGHT_FAILED"}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
