"""Approved recovery of a stale dashboard-only activation lease."""
import argparse
import os
from pathlib import Path
import subprocess

from hope.api.dashboard_deployment import recover_dashboard_activation

ROOT = Path("/opt/hope-operations")
NEW = "hope-m3-dashboard-web.service"
LEGACY = "hope-ops-dashboard-web.service"
SYSTEMCTL = "/usr/bin/systemctl"
COMMAND_ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
                       "HOME": "/nonexistent"}


def systemctl(*arguments: str) -> str:
    return subprocess.run([SYSTEMCTL, *arguments], check=True, timeout=90,
                          capture_output=True, text=True, env=COMMAND_ENVIRONMENT).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover stale dashboard activation; restore prior dashboard")
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--approve-dashboard-recovery", action="store_true", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(2, "DASHBOARD_RECOVERY_ROOT_REQUIRED\n")

    def quiesce() -> None:
        systemctl("stop", NEW, LEGACY)
        if systemctl("show", "--property=ActiveState", "--value", NEW) != "inactive":
            raise RuntimeError("DASHBOARD_NEW_SERVICE_NOT_STOPPED")
        if systemctl("show", "--property=ActiveState", "--value", LEGACY) != "inactive":
            raise RuntimeError("DASHBOARD_LEGACY_SERVICE_NOT_STOPPED")

    recover_dashboard_activation(ROOT, expected_current=args.expected_current,
                                 target_sha=args.target, quiesce=quiesce)
    systemctl("disable", NEW)
    systemctl("enable", LEGACY)
    systemctl("start", LEGACY)
    print("DASHBOARD_ACTIVATION_RECOVERED_PREVIOUS_SERVICE_STARTED")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("DASHBOARD_RECOVERY_FAILED_SERVICES_REQUIRE_OPERATOR_REVIEW")
        raise SystemExit(2)
