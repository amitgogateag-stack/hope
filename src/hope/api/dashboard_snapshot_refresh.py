"""Fail-closed refresh supervisor for a disposable read-only LAB snapshot.

This process has no PAPER scheduler, broker, migration, or write capability.  It
publishes a new snapshot only after exact database-identity and schema-contract
verification.  A failed refresh preserves the prior snapshot and emits only a
closed-vocabulary aggregate status.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from hope.api.dashboard_snapshot import export_dashboard_snapshot
from hope.api.snapshot_contract import verified_snapshot_payload
from hope.infrastructure.durable_file import durable_replace


class _RefreshLockUnavailable(Exception):
    """The refresh publisher boundary could not be acquired safely."""


def _refresh_lock_path(output: Path) -> Path:
    output = Path(output)
    return output.with_name(f".{output.name}.refresh.lock")


def _paths_alias(left: Path, right: Path) -> bool:
    """Compare existing inodes and normalized not-yet-created pathnames."""
    try:
        if os.path.samefile(left, right):
            return True
    except OSError:
        pass
    return left.resolve(strict=False) == right.resolve(strict=False)


def _validated_refresh_paths(output: Path, status: Path) -> tuple[Path, Path]:
    """Keep snapshot, receipt and persistent lock in one disjoint lock domain."""
    output, status = Path(output), Path(status)
    lock = _refresh_lock_path(output)
    if (output.parent.resolve(strict=False) != status.parent.resolve(strict=False)
            or _paths_alias(output, status) or _paths_alias(status, lock)):
        raise ValueError("HOPE_DASHBOARD_REFRESH_PATHS_INVALID")
    return output, status


def _acquire_refresh_lock(output: Path) -> tuple[int, int]:
    """Pin a private publication directory and take its non-blocking flock.

    The lock file is intentionally persistent: unlinking it after release would
    permit two publishers to lock different inodes.  Process death releases the
    kernel lock, so no stale-lock recovery or PID trust is required.
    """
    try:
        import fcntl
    except ImportError as error:  # pragma: no cover - production/CI are Linux
        raise _RefreshLockUnavailable("POSIX_LOCK_REQUIRED") from error
    output = Path(output)
    parent = output.parent
    directory = lock = None
    try:
        directory = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        directory_info = os.fstat(directory)
        if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_mode & 0o022:
            raise _RefreshLockUnavailable("UNTRUSTED_REFRESH_DIRECTORY")
        name = _refresh_lock_path(output).name
        lock = os.open(
            name,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        opened = os.fstat(lock)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        identity = lambda value: (value.st_dev, value.st_ino)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or opened.st_mode & 0o022
            or identity(opened) != identity(named)
        ):
            raise _RefreshLockUnavailable("UNTRUSTED_REFRESH_LOCK")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise _RefreshLockUnavailable("REFRESH_ALREADY_RUNNING") from error
        # Close the final pathname-swap window before treating the lock as held.
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if identity(os.fstat(lock)) != identity(named):
            raise _RefreshLockUnavailable("REFRESH_LOCK_CHANGED")
        return directory, lock
    except _RefreshLockUnavailable:
        if lock is not None:
            os.close(lock)
        if directory is not None:
            os.close(directory)
        raise
    except OSError as error:
        if lock is not None:
            os.close(lock)
        if directory is not None:
            os.close(directory)
        raise _RefreshLockUnavailable("REFRESH_LOCK_UNAVAILABLE") from error


def _release_refresh_lock(descriptors: tuple[int, int]) -> None:
    directory, lock = descriptors
    os.close(lock)
    os.close(directory)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _publish_status(path: Path, payload: dict[str, object]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".hope-dashboard-refresh-", suffix=".json", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        durable_replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _failure_code(error: Exception) -> str:
    if str(error) == "HOPE_DASHBOARD_DATABASE_IDENTITY_MISMATCH":
        return "DATABASE_IDENTITY_MISMATCH"
    if str(error) == "HOPE_DASHBOARD_PAPER_STATE_UNVERIFIED":
        return "PAPER_STATE_UNVERIFIED"
    return "SNAPSHOT_REFRESH_FAILED"


def refresh_once(
    output: Path, status: Path, database_url: str, *, expected_database: str,
    deployed_commit: str, observed_at: datetime | None = None,
) -> bool:
    """Refresh one exact disposable database, preserving last-good on failure."""
    observed_at = observed_at or _utc_now()
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("HOPE_DASHBOARD_REFRESH_TIME_INVALID")
    output, status = _validated_refresh_paths(output, status)
    base = {
        "schema": "hope.dashboard-refresh.v3", "scope": "DISPOSABLE_LAB_ONLY",
        "generated_at": observed_at.astimezone(timezone.utc).isoformat(),
        "deployed_commit": deployed_commit, "paper_only": True,
        "live_enabled": False, "execution_authorized": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        refresh_lock = _acquire_refresh_lock(output)
    except _RefreshLockUnavailable:
        # A contender must not overwrite the active publisher's status receipt.
        # Unsafe lock metadata likewise cannot authorize any publication.
        return False
    staged: Path | None = None
    try:
        descriptor, staged_name = tempfile.mkstemp(
            prefix=".hope-dashboard-refresh-snapshot-", suffix=".json", dir=output.parent,
        )
        os.close(descriptor)
        os.unlink(staged_name)
        staged = Path(staged_name)
        try:
            export_dashboard_snapshot(
                staged, database_url, deployed_commit=deployed_commit,
                data_classification="DISPOSABLE_LAB", expected_database=expected_database,
            )
            staged_payload = staged.read_bytes()
            safe, generated = verified_snapshot_payload(
                staged_payload, expected_commit=deployed_commit, now=observed_at,
            )
            snapshot = json.loads(safe)
            provenance = snapshot.get("provenance", {})
            if provenance.get("data_classification") != "DISPOSABLE_LAB":
                raise ValueError("HOPE_DASHBOARD_REFRESH_PROVENANCE_INVALID")
        except Exception as error:
            staged.unlink(missing_ok=True)
            _publish_status(status, {
                **base, "state": "FAILED", "snapshot_generated_at": None,
                "snapshot_sha256": None, "error_code": _failure_code(error),
                "snapshot_data_classification": None,
                "snapshot_database_identity_sha256": None,
                "snapshot_synthetic_activity": None,
            })
            return False
        durable_replace(staged, output)
        _publish_status(status, {
            **base, "state": "CURRENT", "snapshot_generated_at": generated.isoformat(),
            "snapshot_sha256": hashlib.sha256(staged_payload).hexdigest(),
            "snapshot_data_classification": provenance["data_classification"],
            "snapshot_database_identity_sha256": provenance["database_identity_sha256"],
            "snapshot_synthetic_activity": provenance["synthetic_activity"],
            "error_code": None,
        })
        return True
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        _release_refresh_lock(refresh_lock)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh an isolated HOPE LAB dashboard snapshot")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--deployed-commit", required=True)
    parser.add_argument("--interval-seconds", type=int)
    args = parser.parse_args()
    if args.interval_seconds is not None and not 30 <= args.interval_seconds <= 3600:
        parser.error("--interval-seconds must be between 30 and 3600")
    database_url = os.environ.get("HOPE_DATABASE_URL", "")
    while True:
        refreshed = refresh_once(
            args.output, args.status, database_url,
            expected_database=args.expected_database, deployed_commit=args.deployed_commit,
        )
        if args.interval_seconds is None:
            # One-shot callers (cron, systemd, CI) must be able to distinguish a
            # verified atomic publication from a preserved last-good snapshot.
            return 0 if refreshed else 1
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
