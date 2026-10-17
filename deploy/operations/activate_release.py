"""Operator-invoked code-only activation/rollback; never migrates historical data."""
import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from hope.api.operations_deployment import inspect_release, switch_release
from hope.api.dashboard_server import _verified_snapshot
from hope.api.dashboard_upgrade_probe import verify_dashboard

ROOT = Path("/opt/hope-operations")
HEALTH = Path("/var/lib/hope-paper/health.json")
SNAPSHOT = Path("/var/lib/hope-dashboard-ops/snapshot.json")
UNITS = ("hope-paper.service", "hope-ops-dashboard-web.service", "hope-ops-dashboard-export.timer")


def systemctl(*arguments):
    subprocess.run(["systemctl", *arguments], check=True, timeout=90, capture_output=True)


def metadata(sha):
    with tempfile.NamedTemporaryFile(mode="w", dir=ROOT, delete=False) as stream:
        path = Path(stream.name)
        os.chmod(path, 0o600)
        stream.write(f"HOPE_DEPLOYED_COMMIT={sha}\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(path, ROOT / "deployed.env")


def accepted_health(value, target, started):
    if not isinstance(value, dict):
        return False
    try:
        generated = datetime.fromisoformat(value["generated_at"])
        success = datetime.fromisoformat(value["last_success_at"])
        return (value.get("deployed_commit") == target and value.get("state") == "CURRENT"
                and value.get("schema_compatibility") == "COMPATIBLE"
                and value.get("paper_only") is True and value.get("live_enabled") is False
                and generated.tzinfo is not None and success.tzinfo is not None
                and started <= success <= generated <= datetime.now(UTC))
    except (KeyError, TypeError, ValueError):
        return False


def accepted_dashboard(started):
    """Require a post-switch export and its private loopback presentation."""
    try:
        _, generated = _verified_snapshot(SNAPSHOT)
        modified = datetime.fromtimestamp(SNAPSHOT.stat().st_mtime, UTC)
        result = verify_dashboard("http://127.0.0.1:8788")
        return (modified >= started
                and started - timedelta(minutes=5) <= generated <= datetime.now(UTC)
                and result.get("service") == "RUNNING"
                and result.get("paper_feed") == "AVAILABLE")
    except (OSError, TypeError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Approved code-only switch with rollback; services stop first")
    parser.add_argument("--target", required=True)
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--approve-host-change", action="store_true", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(2, "OPERATIONS_ROOT_REQUIRED\n")
    # Both releases must support the *currently installed* schema. Operator runs
    # database acceptance for each before this wrapper; no down migration exists.
    inspect_release(ROOT / "releases" / args.target, args.target)
    inspect_release(ROOT / "current", args.expected_current)
    def stop_before_switch():
        systemctl("stop", "hope-ops-dashboard-export.timer", "hope-ops-dashboard-export.service", *UNITS[:2])

    def accept():
        metadata(args.target)
        try:
            started = datetime.now(UTC)
            systemctl("start", *UNITS)
            for _ in range(60):
                systemctl("is-active", "--quiet", *UNITS)
                try:
                    value = json.loads(HEALTH.read_text())
                    if accepted_health(value, args.target, started) and accepted_dashboard(started):
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(1)
            raise RuntimeError("OPERATIONS_ACCEPTANCE_TIMEOUT")
        except BaseException:
            try:
                systemctl("stop", *UNITS, "hope-ops-dashboard-export.service")
            finally:
                # Still inside the operation lock; restore metadata before the
                # pointer rollback so another activation cannot interleave it.
                metadata(args.expected_current)
            raise

    switch_release(ROOT, args.target, expected_current=args.expected_current,
                   before_switch=stop_before_switch, accept=accept)
    print("OPERATIONS_CODE_SWITCH_ACCEPTED")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("OPERATIONS_ACTIVATION_FAILED_SERVICES_REQUIRE_OPERATOR_REVIEW")
        raise SystemExit(2)
