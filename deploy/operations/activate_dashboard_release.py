"""Operator-approved dashboard-only activation; never controls PAPER."""
import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import time

from hope.api.dashboard_deployment import switch_dashboard_release
from hope.api.dashboard_server import _verified_snapshot
from hope.api.dashboard_upgrade_probe import verify_dashboard, verify_readiness
from hope.api.operations_deployment import inspect_release

ROOT = Path("/opt/hope-operations")
STATE = Path("/var/lib/hope-dashboard-ops")
UNIT = "hope-m3-dashboard-web.service"
LEGACY_UNIT = "hope-ops-dashboard-web.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT
SYSTEMCTL = "/usr/bin/systemctl"
COMMAND_ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
                       "HOME": "/nonexistent"}


def systemctl(*arguments: str) -> str:
    result = subprocess.run([SYSTEMCTL, *arguments], check=True, timeout=90,
                            capture_output=True, text=True, env=COMMAND_ENVIRONMENT)
    return result.stdout.strip()


def _install_exact_unit(target: Path) -> None:
    source = target / "deploy/operations" / UNIT
    expected = source.read_bytes()
    if UNIT_PATH.exists():
        if UNIT_PATH.is_symlink() or UNIT_PATH.read_bytes() != expected:
            raise RuntimeError("DASHBOARD_INSTALLED_UNIT_MISMATCH")
        return
    temporary = UNIT_PATH.with_suffix(".next")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError("DASHBOARD_UNIT_INSTALL_PENDING")
    with temporary.open("xb") as stream:
        stream.write(expected)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, UNIT_PATH)


def _accepted(target: str) -> bool:
    try:
        if systemctl("is-active", UNIT) != "active":
            return False
        pointer = (ROOT / "dashboard-current").resolve(strict=True)
        if pointer != ROOT / "releases" / target:
            return False
        payload, generated = _verified_snapshot(STATE / "snapshot.json")
        if not 0 <= (datetime.now(UTC) - generated).total_seconds() <= 3600:
            return False
        result = verify_dashboard("http://127.0.0.1:8788", activation_started=generated,
                                  activation_snapshot=payload)
        readiness = verify_readiness("http://127.0.0.1:8788")
        return (result["service"] == "RUNNING" and readiness["server"] == "RUNNING"
                and readiness["paper_only"] is True and readiness["live_enabled"] is False
                and readiness["execution_authorized"] is False)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError):
        return False


def main() -> None:
    # Compatibility entry point: one canonical guarded activation lifecycle.
    from hope.api.dashboard_activation import main as dashboard_main
    parser = argparse.ArgumentParser(description="Reviewed dashboard-only activation with durable journal")
    parser.add_argument("--target", required=True)
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--ci-run-id", type=int, required=True)
    parser.add_argument("--ci-run-attempt", type=int, required=True)
    parser.add_argument("--approve-dashboard-only-host-change", action="store_true", required=True)
    args = parser.parse_args()
    return dashboard_main(["activate", "--target", args.target,
        "--expected-current", args.expected_current, "--ci-run-id", str(args.ci_run_id),
        "--ci-run-attempt", str(args.ci_run_attempt), "--approve-dashboard-change"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("M3_DASHBOARD_ACTIVATION_FAILED_ROLLBACK_ATTEMPTED")
        raise SystemExit(2)
