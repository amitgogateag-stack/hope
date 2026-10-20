#!/usr/bin/env python3
"""Read-only preflight for the M3 dashboard exporter installation.

This deliberately never installs, starts, enables, or changes a unit. Its JSON
verdict is diagnostic only; it is not permission to deploy or activate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path("/opt/hope-operations")
UNIT_DIR = Path("/etc/systemd/system")
NAMES = ("hope-ops-dashboard-export.service", "hope-ops-dashboard-export.timer")
EXPECTED_DB = "hope_m3_restore_test"
MAX_BYTES = 65536
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
FROZEN_BASELINE = "7e9d341742818a97accce3e2301b1c7f96596795"
EXPECTED_TEMPLATE_HASHES = {
    "hope-ops-dashboard-export.service":
        "dcb3ddc3e21b875021eb86f5a1f755334c6fc6e0ebd3b056aa4c1ac3365389e8",
    "hope-ops-dashboard-export.timer":
        "8de0be15a995a1e3ec8de852e8743142fa8556c6dfbfbbb01a52d8c763a5ee17",
}


def _open_directory(path: Path, *, owner_uid: int) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    opened = os.fstat(fd)
    if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != owner_uid
            or opened.st_mode & 0o022):
        os.close(fd)
        raise ValueError("RELEASE_DIRECTORY_UNSAFE")
    return fd


def _open_directory_at(directory_fd: int, name: str, *, owner_uid: int) -> int:
    fd = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    opened = os.fstat(fd)
    if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != owner_uid
            or opened.st_mode & 0o022):
        os.close(fd)
        raise ValueError("RELEASE_DIRECTORY_UNSAFE")
    return fd


def _verify_private_file(path: Path, *, owner_uid: int = 0) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise ValueError("CONFIG_UNSAFE") from None
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_uid != owner_uid or opened.st_mode & 0o077):
            raise ValueError("CONFIG_UNSAFE")
    finally:
        os.close(fd)


def _read_regular_at(directory_fd: int, name: str, *, owner_uid: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != owner_uid or before.st_mode & 0o022
                or before.st_size <= 0 or before.st_size > MAX_BYTES):
            raise ValueError("RELEASE_FILE_UNSAFE")
        chunks = []
        remaining = MAX_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(fd)
        identity = lambda value: (
            value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns,
        )
        if len(payload) > MAX_BYTES or identity(before) != identity(after):
            raise ValueError("RELEASE_FILE_CHANGED")
        return payload
    finally:
        os.close(fd)


def verify_release(expected: str, *, owner_uid: int = 0) -> None:
    """Bind inspection to one immutable-looking reviewed release on this host."""
    if not SHA_RE.fullmatch(expected) or expected == FROZEN_BASELINE:
        raise ValueError("EXPECTED_SHARED_INVALID")
    root_fd = _open_directory(ROOT, owner_uid=owner_uid)
    releases_fd = release_fd = -1
    try:
        releases_fd = _open_directory_at(root_fd, "releases", owner_uid=owner_uid)
        release_fd = _open_directory_at(releases_fd, expected, owner_uid=owner_uid)
        release = os.fstat(release_fd)

        current = ROOT / "current"
        if not current.is_symlink():
            raise ValueError("SHARED_POINTER_UNVERIFIED")
        current_stat = current.stat()
        if (current_stat.st_dev, current_stat.st_ino) != (release.st_dev, release.st_ino):
            raise ValueError("SHARED_POINTER_MISMATCH")

        deployed = _read_regular_at(root_fd, "deployed.env", owner_uid=owner_uid)
        if deployed != f"HOPE_DEPLOYED_COMMIT={expected}\n".encode("ascii"):
            raise ValueError("DEPLOYED_IDENTITY_MISMATCH")

        operations_fd = deploy_fd = -1
        try:
            deploy_fd = _open_directory_at(release_fd, "deploy", owner_uid=owner_uid)
            operations_fd = _open_directory_at(
                deploy_fd, "operations", owner_uid=owner_uid,
            )
            for name, expected_hash in EXPECTED_TEMPLATE_HASHES.items():
                payload = _read_regular_at(operations_fd, name, owner_uid=owner_uid)
                if hashlib.sha256(payload).hexdigest() != expected_hash:
                    raise ValueError("TEMPLATE_IDENTITY_MISMATCH")
        finally:
            if operations_fd >= 0:
                os.close(operations_fd)
            if deploy_fd >= 0:
                os.close(deploy_fd)

        final_current = current.stat()
        if (final_current.st_dev, final_current.st_ino) != (release.st_dev, release.st_ino):
            raise ValueError("SHARED_POINTER_CHANGED")
    finally:
        if release_fd >= 0:
            os.close(release_fd)
        if releases_fd >= 0:
            os.close(releases_fd)
        os.close(root_fd)


def unit_metadata(name: str) -> dict[str, str]:
    p = subprocess.run(
        ["/usr/bin/systemctl", "show", name,
         "--property=LoadState,ActiveState,UnitFileState,FragmentPath,DropInPaths",
         "--no-pager"],
        capture_output=True, timeout=8, check=False, env={"PATH": "/usr/bin:/bin"},
    )
    if p.returncode or len(p.stdout) > MAX_BYTES:
        raise ValueError("UNIT_METADATA_UNVERIFIED")
    result = {}
    for line in p.stdout.decode("utf-8", errors="strict").splitlines():
        if "=" not in line:
            raise ValueError("UNIT_METADATA_MALFORMED")
        key, value = line.split("=", 1)
        if key in result:
            raise ValueError("UNIT_METADATA_DUPLICATE")
        result[key] = value
    if set(result) != {"LoadState", "ActiveState", "UnitFileState", "FragmentPath", "DropInPaths"}:
        raise ValueError("UNIT_METADATA_INCOMPLETE")
    return result


def main(argv: list[str] | None = None) -> int:
    failures = []
    try:
        arguments = sys.argv[1:] if argv is None else argv
        if (len(arguments) != 2 or arguments[0] != "--expected-shared"
                or not SHA_RE.fullmatch(arguments[1])):
            raise ValueError("EXPECTED_SHARED_REQUIRED")
        if os.geteuid() != 0:
            raise ValueError("ROOT_INSPECTION_REQUIRED")
        expected = arguments[1]
        verify_release(expected)
        for name in NAMES:
            path = UNIT_DIR / name
            if path.exists() or path.is_symlink():
                failures.append("UNIT_PATH_ALREADY_EXISTS:" + name)
            info = unit_metadata(name)
            if (info["LoadState"] != "not-found"
                    or info["ActiveState"] != "inactive"
                    or info["FragmentPath"] or info["DropInPaths"]):
                failures.append("UNIT_NOT_ABSENT:" + name)
        config = Path("/etc/hope-dashboard-ops.env")
        try:
            _verify_private_file(config)
        except (OSError, ValueError):
            failures.append("CONFIG_UNVERIFIED")
        for directory in (ROOT / "current", Path("/var/lib/hope-dashboard-ops")):
            if not directory.exists():
                failures.append("PATH_MISSING")
        # No claim of permission to install: explicit operator review is mandatory.
        result = "REVIEW_REQUIRED" if not failures else "BLOCKED"
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
        result = "BLOCKED"
        failures.append("INSPECTION_UNVERIFIED")
    print(json.dumps({"schema_version": 1, "result": result,
                      "findings": sorted(set(failures)),
                      "release_identity": "VERIFIED" if not failures else "NOT_VERIFIED",
                      "database_identity": "NOT_VERIFIED",
                      "paper_isolation": "NOT_VERIFIED",
                      "host_changes": False}, sort_keys=True))
    return 2


if __name__ == "__main__":
    sys.exit(main())
