"""Operator-invoked code-only activation/rollback; never migrates historical data."""
import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import stat
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
SYSTEMCTL = "/usr/bin/systemctl"
COMMAND_ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
                       "HOME": "/nonexistent"}


def systemctl(*arguments):
    subprocess.run([SYSTEMCTL, *arguments], check=True, timeout=90, capture_output=True,
                   env=COMMAND_ENVIRONMENT)


def _metadata_file(fd, expected_uid, expected_payload, expected_identity=None):
    value = os.fstat(fd)
    identity = (value.st_dev, value.st_ino)
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != expected_uid
            or stat.S_IMODE(value.st_mode) != 0o600 or value.st_nlink != 1
            or value.st_size != len(expected_payload)
            or (expected_identity is not None and identity != expected_identity)):
        raise RuntimeError("OPERATIONS_METADATA_IDENTITY_INVALID")
    os.lseek(fd, 0, os.SEEK_SET)
    if os.read(fd, len(expected_payload) + 1) != expected_payload:
        raise RuntimeError("OPERATIONS_METADATA_CONTENT_INVALID")
    return identity


def metadata(sha):
    """Durably publish authenticated deployment metadata inside the trusted root."""
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ValueError("OPERATIONS_METADATA_COMMIT_INVALID")
    payload = f"HOPE_DEPLOYED_COMMIT={sha}\n".encode("ascii")
    expected_uid = os.geteuid()
    root_descriptor = None
    staged_path = None
    staged_identity = None
    published = False
    try:
        root_descriptor = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        with tempfile.NamedTemporaryFile(
            mode="w+b", dir=ROOT, prefix=".deployed.env.", delete=False
        ) as stream:
            staged_path = Path(stream.name)
            value = os.fstat(stream.fileno())
            staged_identity = (value.st_dev, value.st_ino)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            _metadata_file(
                stream.fileno(), expected_uid, payload, expected_identity=staged_identity
            )
            os.fsync(stream.fileno())
            _metadata_file(
                stream.fileno(), expected_uid, payload, expected_identity=staged_identity
            )
        staged_name = staged_path.name
        value = os.stat(staged_name, dir_fd=root_descriptor, follow_symlinks=False)
        if (value.st_dev, value.st_ino) != staged_identity:
            raise RuntimeError("OPERATIONS_METADATA_STAGING_REPLACED")
        os.rename(
            staged_name,
            "deployed.env",
            src_dir_fd=root_descriptor,
            dst_dir_fd=root_descriptor,
        )
        published = True
        published_descriptor = os.open(
            "deployed.env", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_descriptor
        )
        try:
            _metadata_file(
                published_descriptor,
                expected_uid,
                payload,
                expected_identity=staged_identity,
            )
        finally:
            os.close(published_descriptor)
        os.fsync(root_descriptor)
    except OSError as error:
        raise RuntimeError("OPERATIONS_METADATA_DURABILITY_FAILED") from error
    finally:
        if root_descriptor is not None:
            if staged_path is not None and not published:
                try:
                    value = os.stat(
                        staged_path.name,
                        dir_fd=root_descriptor,
                        follow_symlinks=False,
                    )
                    if (value.st_dev, value.st_ino) == staged_identity:
                        os.unlink(staged_path.name, dir_fd=root_descriptor)
                except FileNotFoundError:
                    pass
            os.close(root_descriptor)


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
        payload, generated = _verified_snapshot(SNAPSHOT)
        modified = datetime.fromtimestamp(SNAPSHOT.stat().st_mtime, UTC)
        current = datetime.now(UTC)
        if not (modified >= started and started <= generated <= current):
            return False
        result = verify_dashboard("http://127.0.0.1:8788", activation_started=started,
                                  activation_snapshot=payload)
        return (result.get("service") == "RUNNING"
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
