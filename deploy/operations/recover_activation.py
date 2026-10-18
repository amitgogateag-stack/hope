"""Operator-approved stale activation lease recovery; never starts services."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess

from hope.api.operations_deployment import recover_stale_activation

ROOT = Path("/opt/hope-operations")
UNITS = ("hope-ops-dashboard-export.timer", "hope-ops-dashboard-export.service",
         "hope-paper.service", "hope-ops-dashboard-web.service")
SYSTEMCTL = "/usr/bin/systemctl"
COMMAND_ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
                       "HOME": "/nonexistent"}


def systemctl(*arguments: str) -> str:
    result = subprocess.run([SYSTEMCTL, *arguments], check=True, timeout=90,
                            capture_output=True, text=True, env=COMMAND_ENVIRONMENT)
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove one verified stale activation lease with services stopped")
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--approve-host-change", action="store_true", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(2, "OPERATIONS_ROOT_REQUIRED\n")

    def quiesce() -> None:
        systemctl("stop", *UNITS)
        for unit in UNITS:
            if systemctl("show", "--property=ActiveState", "--value", unit) not in {
                "inactive", "failed"
            }:
                raise RuntimeError("OPERATIONS_SERVICE_NOT_STOPPED")

    recover_stale_activation(ROOT, expected_current=args.expected_current,
                             target_sha=args.target, quiesce=quiesce)
    print("OPERATIONS_STALE_ACTIVATION_RECOVERED_SERVICES_STOPPED")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("OPERATIONS_ACTIVATION_RECOVERY_FAILED_SERVICES_REQUIRE_OPERATOR_REVIEW")
        raise SystemExit(2)
