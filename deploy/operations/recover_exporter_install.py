#!/usr/bin/env python3
"""Rollback an authenticated interrupted exporter install; never retry it."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path

ROOT = Path("/opt/hope-operations")
UNIT_DIR = Path("/etc/systemd/system")


class RecoveryError(RuntimeError):
    """Fail-closed exporter recovery error."""


def _load(name: str):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("hope_" + name, path)
    if spec is None or spec.loader is None:
        raise RecoveryError("EXPORTER_RECOVERY_COMPONENT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recover_exporter_install(
    expected: str,
    attempt: str,
    *,
    root: Path = ROOT,
    unit_dir: Path = UNIT_DIR,
    owner_uid: int = 0,
    daemon_reload=None,
    inspector=None,
    installer=None,
    journal_module=None,
) -> tuple[str, ...]:
    """Rollback only v2 journaled inodes and retain terminal evidence."""
    inspector = inspector or _load("inspect_exporter_install")
    installer = installer or _load("install_exporter_units")
    journal_module = journal_module or _load("exporter_install_journal")
    if daemon_reload is None:
        daemon_reload = installer._daemon_reload

    inspector.ROOT = root
    inspector.verify_release(expected, owner_uid=owner_uid)
    root_fd = inspector._open_directory(root, owner_uid=owner_uid)
    releases_fd = release_fd = deploy_fd = operations_fd = unit_fd = -1
    journal = None
    try:
        journal = journal_module.InstallJournal.open_recovery(
            root_fd, root, expected, unit_dir, attempt, owner_uid=owner_uid
        )
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

        payloads = {}
        for name in installer.NAMES:
            payload = inspector._read_regular_at(
                operations_fd, name, owner_uid=owner_uid
            )
            if (hashlib.sha256(payload).hexdigest()
                    != inspector.EXPECTED_TEMPLATE_HASHES[name]):
                raise RecoveryError("EXPORTER_RECOVERY_TEMPLATE_MISMATCH")
            payloads[name] = payload

        inspector._verify_directory_at(
            deploy_fd, "operations", operations_fd, owner_uid=owner_uid
        )
        inspector._verify_directory_path(unit_dir, unit_fd, owner_uid=owner_uid)
        # Re-prove the authoritative current release after every recovery input
        # has been read and immediately before any durable recovery intent.
        inspector.verify_release(expected, owner_uid=owner_uid)
        entries = []
        journaled_names = {unit["name"] for unit in journal.data["units"]}
        for name in installer.NAMES:
            if name not in journaled_names and (
                not installer._absent(name, unit_fd)
                or installer._unit_state(inspector, name) != inspector.ABSENT_UNIT
            ):
                raise RecoveryError("EXPORTER_RECOVERY_PEER_CONFLICT")
        for unit in journal.data["units"]:
            digest = hashlib.sha256(payloads[unit["name"]]).hexdigest()
            if digest != unit["sha256"]:
                raise RecoveryError("EXPORTER_RECOVERY_TEMPLATE_MISMATCH")
            identity = (
                unit["device"], unit["inode"], unit["size"],
                unit["mtime_ns"], unit["ctime_ns"],
            )
            if not unit["published"]:
                try:
                    os.stat(unit["name"], dir_fd=unit_fd,
                            follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise RecoveryError(
                        "EXPORTER_RECOVERY_PUBLICATION_AMBIGUOUS"
                    )
            entries.append((unit["name"], identity, digest))

        if journal.data["phase"] != "RECOVERY_PENDING":
            journal.record("RECOVERY_PENDING", entries)
        try:
            installer._rollback(
                unit_fd, entries, payloads, inspector,
                owner_uid=owner_uid, unit_dir=unit_dir,
                daemon_reload=daemon_reload,
            )
        except BaseException as exc:
            if journal.healthy and journal.data["phase"] == "RECOVERY_PENDING":
                try:
                    journal.record("ROLLBACK_UNVERIFIED", entries)
                except Exception:
                    pass
            raise RecoveryError("EXPORTER_RECOVERY_ROLLBACK_UNVERIFIED") from exc
        journal.record("RECOVERED", entries)
        return tuple(entry[0] for entry in entries)
    finally:
        if journal is not None:
            journal.close()
        for fd in (unit_fd, operations_fd, deploy_fd, release_fd,
                   releases_fd, root_fd):
            if fd >= 0:
                os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-shared", required=True)
    parser.add_argument("--expected-attempt", required=True)
    parser.add_argument("--approve-exporter-recovery", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0 or not args.approve_exporter_recovery:
        print("EXPORTER_RECOVERY_EXPLICIT_ROOT_APPROVAL_REQUIRED")
        return 2
    try:
        recovered = recover_exporter_install(
            args.expected_shared, args.expected_attempt
        )
    except BaseException:
        print("EXPORTER_RECOVERY_BLOCKED")
        return 2
    print("EXPORTER_RECOVERY_COMPLETED_REINSTALL_BLOCKED:" + ",".join(recovered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
