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
import stat
import subprocess
from pathlib import Path
from typing import Callable

from sqlalchemy import Connection, text


class DeploymentError(RuntimeError):
    pass


def _sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DeploymentError("OPERATIONS_COMMIT_INVALID")
    return value


def _git(release: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-c", f"safe.directory={release}",
                                 "-C", str(release), *arguments],
                                check=True, capture_output=True, text=True, timeout=15)
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


@contextmanager
def _activation_guard(root: Path):
    """Serialize lease creation/removal on a persistent, never-unlinked inode.

    Fingerprints cannot make pathname unlink conditional. Both activation and
    stale recovery must hold this kernel lock across every lease operation.
    Process death releases flock; the separate authenticated lease survives.
    """
    try:
        import fcntl
    except ImportError as exc:
        raise DeploymentError("OPERATIONS_ACTIVATION_POSIX_REQUIRED") from exc
    path = root.resolve(strict=True) / ".activation.guard"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
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
        os.close(descriptor)


@contextmanager
def activation_lock(root: Path, *, expected_current: str, target_sha: str):
    """Exclusive authenticated operation lease, retained after process death."""
    with _activation_guard(root):
        with _create_activation_lease(root, expected_current=expected_current, target_sha=target_sha):
            yield


@contextmanager
def _create_activation_lease(root: Path, *, expected_current: str, target_sha: str):
    expected_current, target_sha = _sha(expected_current), _sha(target_sha)
    path = root.resolve(strict=True) / ".activation.lock"
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
    try:
        os.write(descriptor, record)
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        path.unlink()


def inspect_activation_recovery(root: Path, *, proc_root: Path = Path("/proc")) -> dict[str, object]:
    """Diagnose an interrupted activation without removing locks or changing services."""
    root = root.resolve(strict=True)
    path = root / ".activation.lock"
    if not path.exists() and not path.is_symlink():
        return {"schema_version": 1, "state": "ABSENT", "recovery_safe": True}
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022 or info.st_size > 4096):
        raise DeploymentError("OPERATIONS_ACTIVATION_LOCK_UNTRUSTED")
    try:
        record = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
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
    try:
        content = deployed.read_text() if deployed.is_file() and not deployed.is_symlink() else ""
    except OSError:
        content = ""
    metadata = ("EXPECTED" if content == f"HOPE_DEPLOYED_COMMIT={expected}\n" else
                "TARGET" if content == f"HOPE_DEPLOYED_COMMIT={target}\n" else "OTHER")
    pending = "PRESENT" if (root / ".current.next").exists() or (root / ".current.next").is_symlink() else "ABSENT"
    safe = lock_state == "STALE" and pointer == metadata == "EXPECTED" and pending == "ABSENT"
    return {
        "schema_version": 1, "state": lock_state, "expected_current": expected,
        "target_sha": target, "current_pointer": pointer, "deployed_metadata": metadata,
        "pending_pointer": pending, "recovery_safe": safe,
    }


def _activation_lock_fingerprint(root: Path) -> tuple[int, int, int, int, str]:
    """Bind recovery to the exact regular-file lease that was inspected."""
    path = root / ".activation.lock"
    info = path.lstat()
    content = path.read_bytes()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            hashlib.sha256(content).hexdigest())


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
        (root / ".activation.lock").unlink()
        descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
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

    def point(path: Path) -> None:
        os.symlink(path, temporary, target_is_directory=True)
        os.replace(temporary, current)

    try:
        before_switch()
        point(target)
        try:
            accept()
        except BaseException:
            point(previous)
            raise
    finally:
        if temporary.is_symlink():
            temporary.unlink()


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
