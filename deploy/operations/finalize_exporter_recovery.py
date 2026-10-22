#!/usr/bin/env python3
"""Archive exact recovered exporter evidence; never install or activate units."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path

ROOT = Path("/opt/hope-operations")
UNIT_DIR = Path("/etc/systemd/system")


class FinalizationError(RuntimeError):
    """Fail-closed exporter recovery-finalization error."""


def _load(name: str):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("hope_" + name, path)
    if spec is None or spec.loader is None:
        raise FinalizationError("EXPORTER_FINALIZATION_COMPONENT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finalize_exporter_recovery(
    expected: str,
    attempt: str,
    *,
    root: Path = ROOT,
    unit_dir: Path = UNIT_DIR,
    owner_uid: int = 0,
    inspector=None,
    installer=None,
    journal_module=None,
) -> str:
    """Archive only exact RECOVERED evidence after proving canonical absence."""
    inspector = inspector or _load("inspect_exporter_install")
    installer = installer or _load("install_exporter_units")
    journal_module = journal_module or _load("exporter_install_journal")

    inspector.ROOT = root
    inspector.verify_release(expected, owner_uid=owner_uid)
    root_fd = inspector._open_directory(root, owner_uid=owner_uid)
    unit_fd = -1
    journal = None
    try:
        journal = journal_module.InstallJournal.open_finalization(
            root_fd, root, expected, unit_dir, attempt, owner_uid=owner_uid
        )
        unit_fd = inspector._open_directory(unit_dir, owner_uid=owner_uid)
        inspector._verify_directory_path(unit_dir, unit_fd, owner_uid=owner_uid)
        for name in installer.NAMES:
            if (not installer._absent(name, unit_fd)
                    or installer._unit_state(inspector, name)
                    != inspector.ABSENT_UNIT):
                raise FinalizationError("EXPORTER_FINALIZATION_UNIT_NOT_ABSENT")

        # Re-prove all mutable authority immediately before clearing the live
        # journal name. The archived inode remains private and immutable by this
        # command; a later installer starts with a distinct attempt identity.
        inspector._verify_directory_path(unit_dir, unit_fd, owner_uid=owner_uid)
        inspector.verify_release(expected, owner_uid=owner_uid)
        return journal.archive_recovered()
    finally:
        if journal is not None:
            journal.close()
        if unit_fd >= 0:
            os.close(unit_fd)
        os.close(root_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-shared", required=True)
    parser.add_argument("--expected-attempt", required=True)
    parser.add_argument("--approve-exporter-finalization", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0 or not args.approve_exporter_finalization:
        print("EXPORTER_FINALIZATION_EXPLICIT_ROOT_APPROVAL_REQUIRED")
        return 2
    try:
        archive = finalize_exporter_recovery(
            args.expected_shared, args.expected_attempt
        )
    except BaseException:
        print("EXPORTER_FINALIZATION_BLOCKED")
        return 2
    print("EXPORTER_RECOVERY_FINALIZED:" + archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
