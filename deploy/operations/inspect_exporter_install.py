#!/usr/bin/env python3
"""Read-only preflight for the M3 dashboard exporter installation.

This deliberately never installs, starts, enables, or changes a unit. Its JSON
verdict is diagnostic only; it is not permission to deploy or activate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path("/opt/hope-operations")
UNIT_DIR = Path("/etc/systemd/system")
NAMES = ("hope-ops-dashboard-export.service", "hope-ops-dashboard-export.timer")
EXPECTED_DB = "hope_m3_restore_test"
MAX_BYTES = 65536


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


def main() -> int:
    failures = []
    try:
        if not (ROOT / "current").is_symlink():
            failures.append("SHARED_POINTER_UNVERIFIED")
        for name in NAMES:
            path = UNIT_DIR / name
            if path.exists() or path.is_symlink():
                failures.append("UNIT_PATH_ALREADY_EXISTS:" + name)
            info = unit_metadata(name)
            if (info["LoadState"] != "not-found"
                    or info["ActiveState"] != "inactive"
                    or info["FragmentPath"] or info["DropInPaths"]):
                failures.append("UNIT_NOT_ABSENT:" + name)
        for name in NAMES:
            source = ROOT / "current" / "deploy" / "operations" / name
            if not source.is_file() or source.is_symlink() or source.stat().st_size > MAX_BYTES:
                failures.append("TEMPLATE_UNVERIFIED:" + name)
        config = Path("/etc/hope-dashboard-ops.env")
        if not config.is_file() or config.is_symlink():
            failures.append("CONFIG_UNVERIFIED")
        else:
            # Never read or print credentials. Connection validation is a separate
            # restricted-account preflight, not this root-level metadata check.
            mode = config.stat().st_mode & 0o777
            if mode & 0o077:
                failures.append("CONFIG_PERMISSIONS_UNSAFE")
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
                      "database_identity": "NOT_VERIFIED",
                      "paper_isolation": "NOT_VERIFIED",
                      "host_changes": False}, sort_keys=True))
    return 2


if __name__ == "__main__":
    sys.exit(main())
