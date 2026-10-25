"""Read-only AWS host reconciliation for a reviewed HOPE release.

This module deliberately observes only public filesystem metadata, fixed systemd
properties, and the bounded loopback operations status document.  It never
loads environment files, imports a PAPER factory, or changes host state.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
import math
from pathlib import Path
import re
import subprocess
import time
from typing import Callable

from hope.api import operations_deployment
from hope.api.diagnostic_io import bounded_capture
from hope.api.operations_deployment import DeploymentError, inspect_release


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_STATUS_BYTES = 16_384
MAX_SYSTEMD_BYTES = 4_096
SYSTEMD_OBSERVATION_TIMEOUT = 5.0
REQUIRED_UNITS = (
    "postgresql.service",
    "hope-dashboard-web.service",
    "hope-ops-dashboard-web.service",
    "hope-ops-dashboard-export.service",
    "hope-ops-dashboard-export.timer",
    "hope-paper.service",
)
RUNNING_UNITS = (
    "postgresql.service",
    "hope-dashboard-web.service",
    "hope-ops-dashboard-web.service",
    "hope-paper.service",
)
PERSISTENT_UNITS = (
    "postgresql.service",
    "hope-dashboard-web.service",
    "hope-ops-dashboard-web.service",
    "hope-ops-dashboard-export.timer",
    "hope-paper.service",
)
SERVICE_RECOVERY_POLICIES = {
    "hope-dashboard-web.service": {
        "Type": "simple", "Restart": "on-failure", "RestartUSec": "5s",
    },
    "hope-ops-dashboard-web.service": {
        "Type": "simple", "Restart": "on-failure", "RestartUSec": "10s",
    },
    "hope-paper.service": {
        "Type": "notify", "Restart": "always", "RestartUSec": "15s",
        "WatchdogUSec": "15min",
    },
}


class ReadinessError(RuntimeError):
    """The observation could not be authenticated or safely bounded."""


def _deployed_release(root: Path) -> tuple[Path, str]:
    current = root / "current"
    if not current.is_symlink():
        raise ReadinessError("AWS_CURRENT_RELEASE_SYMLINK_REQUIRED")
    releases = (root / "releases").resolve(strict=True)
    deployed = current.resolve(strict=True)
    if deployed.parent != releases or not SHA_RE.fullmatch(deployed.name):
        raise ReadinessError("AWS_CURRENT_RELEASE_TARGET_UNTRUSTED")
    return deployed, deployed.name


def _systemd_probe(
    unit: str,
    *,
    timeout: float = SYSTEMD_OBSERVATION_TIMEOUT,
    capture: Callable[..., str] = bounded_capture,
) -> dict[str, str]:
    if (not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ReadinessError("AWS_SYSTEMD_OBSERVATION_TIMEOUT:" + unit)
    timeout = min(float(timeout), SYSTEMD_OBSERVATION_TIMEOUT)
    base_properties = (
        "LoadState", "ActiveState", "SubState", "UnitFileState", "NRestarts",
    )
    requested_properties = base_properties + tuple(
        SERVICE_RECOVERY_POLICIES.get(unit, ())
    )
    command = ["/usr/bin/systemctl", "show", unit, "--no-pager"]
    for property_name in requested_properties:
        command.extend(("-p", property_name))
    try:
        stdout = capture(
            command,
            timeout=timeout,
            max_output=MAX_SYSTEMD_BYTES,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except RuntimeError as exc:
        if str(exc) == "DIAGNOSTIC_TIMEOUT":
            raise ReadinessError("AWS_SYSTEMD_OBSERVATION_TIMEOUT:" + unit) from exc
        if str(exc) == "DIAGNOSTIC_OUTPUT_LIMIT":
            raise ReadinessError("AWS_SYSTEMD_OBSERVATION_TOO_LARGE:" + unit) from exc
        raise ReadinessError("AWS_SYSTEMD_OBSERVATION_FAILED:" + unit) from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReadinessError("AWS_SYSTEMD_OBSERVATION_FAILED:" + unit) from exc
    except UnicodeError as exc:
        raise ReadinessError("AWS_SYSTEMD_OBSERVATION_INVALID:" + unit) from exc
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in requested_properties:
            values[key] = value
    if (set(values) != set(requested_properties)
            or not re.fullmatch(r"[0-9]{1,20}", values["NRestarts"])):
        raise ReadinessError("AWS_SYSTEMD_OBSERVATION_INVALID:" + unit)
    return values


def _status_probe(url: str) -> dict[str, object]:
    if url != "http://127.0.0.1:8788/status.json":
        raise ReadinessError("AWS_STATUS_URL_NOT_APPROVED")
    connection = HTTPConnection("127.0.0.1", 8788, timeout=5)
    try:
        connection.request("GET", "/status.json", headers={"Host": "127.0.0.1"})
        response = connection.getresponse()
        if response.status != 200:
            raise ReadinessError("AWS_STATUS_HTTP_FAILED:" + str(response.status))
        raw = response.read(MAX_STATUS_BYTES + 1)
    finally:
        connection.close()
    if len(raw) > MAX_STATUS_BYTES:
        raise ReadinessError("AWS_STATUS_DOCUMENT_TOO_LARGE")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReadinessError("AWS_STATUS_DOCUMENT_INVALID") from exc
    if not isinstance(document, dict):
        raise ReadinessError("AWS_STATUS_DOCUMENT_INVALID")
    return document


def inspect_aws_readiness(
    expected_sha: str,
    *,
    root: Path = Path("/opt/hope-operations"),
    now: datetime | None = None,
    systemd_probe: Callable[..., dict[str, str]] = _systemd_probe,
    status_probe: Callable[[str], dict[str, object]] = _status_probe,
    release_probe: Callable[[Path, str], dict[str, object]] = inspect_release,
    status_url: str = "http://127.0.0.1:8788/status.json",
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Return a sanitized, deterministic readiness report without host writes."""
    if not SHA_RE.fullmatch(expected_sha):
        raise ReadinessError("AWS_EXPECTED_SHA_REQUIRED")
    if status_url != "http://127.0.0.1:8788/status.json":
        raise ReadinessError("AWS_STATUS_URL_NOT_APPROVED")
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ReadinessError("AWS_OBSERVATION_TIME_INVALID")
    observed_at = observed_at.astimezone(timezone.utc)

    # The release pointer, unit state, and dashboard document form one
    # operational snapshot.  Every supported activation holds the exclusive
    # side of this fence, so a readiness report must hold the shared side from
    # release authentication through the final status observation.  Otherwise
    # it could combine a release from before a switch with services or status
    # from after it and incorrectly report READY.
    try:
        with operations_deployment._activation_guard(root, shared=True):
            return _inspect_stable_readiness(
                expected_sha, root=root, observed_at=observed_at,
                systemd_probe=systemd_probe, status_probe=status_probe,
                release_probe=release_probe, status_url=status_url,
                monotonic=monotonic,
            )
    except DeploymentError as exc:
        raise ReadinessError("AWS_ACTIVATION_STATE_UNAVAILABLE") from exc


def _inspect_stable_readiness(
    expected_sha: str,
    *,
    root: Path,
    observed_at: datetime,
    systemd_probe: Callable[..., dict[str, str]],
    status_probe: Callable[[str], dict[str, object]],
    release_probe: Callable[[Path, str], dict[str, object]],
    status_url: str,
    monotonic: Callable[[], float],
) -> dict[str, object]:
    """Observe one generation while the caller holds the shared fence."""
    deployed_release, deployed_sha = _deployed_release(root)
    try:
        release_probe(deployed_release, deployed_sha)
    except (OSError, DeploymentError) as exc:
        raise ReadinessError("AWS_DEPLOYED_RELEASE_UNVERIFIED") from exc
    blockers: list[str] = []
    if deployed_sha != expected_sha:
        blockers.append("DEPLOYED_SHA_MISMATCH")

    units: dict[str, dict[str, str]] = {}
    systemd_deadline = monotonic() + SYSTEMD_OBSERVATION_TIMEOUT
    for unit in REQUIRED_UNITS:
        remaining = systemd_deadline - monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            raise ReadinessError("AWS_SYSTEMD_OBSERVATION_TIMEOUT:" + unit)
        values = systemd_probe(unit, timeout=remaining)
        units[unit] = values
        if values["LoadState"] != "loaded":
            blockers.append("UNIT_NOT_LOADED:" + unit)

    # A process that happens to be active now is not autonomous after reboot if
    # its unit is disabled.  The exporter service is intentionally static and
    # timer-triggered; every long-running unit and that timer must be enabled.
    for unit in PERSISTENT_UNITS:
        if (units[unit]["LoadState"] == "loaded"
                and units[unit]["UnitFileState"] != "enabled"):
            blockers.append("UNIT_NOT_ENABLED:" + unit)

    for unit in RUNNING_UNITS:
        if units[unit]["LoadState"] != "loaded":
            continue
        if units[unit]["ActiveState"] != "active":
            blockers.append("UNIT_NOT_ACTIVE:" + unit)
        elif units[unit]["SubState"] != "running":
            blockers.append("UNIT_NOT_RUNNING:" + unit)

    # Current liveness is not crash recovery.  Authenticate the effective
    # systemd policy, including drop-in overrides, against the reviewed units.
    for unit, policy in SERVICE_RECOVERY_POLICIES.items():
        if units[unit]["LoadState"] != "loaded":
            continue
        if any(units[unit].get(key) != value for key, value in policy.items()):
            blockers.append("UNIT_RECOVERY_POLICY_MISMATCH:" + unit)
    timer = "hope-ops-dashboard-export.timer"
    if units[timer]["LoadState"] == "loaded" and units[timer]["ActiveState"] != "active":
        blockers.append("UNIT_NOT_ACTIVE:" + timer)

    status = status_probe(status_url)
    safe_status = {
        key: status[key]
        for key in ("schema_version", "service", "data_feed", "snapshot_generated_at")
        if key in status
    }
    if status.get("schema_version") != 1 or status.get("service") != "RUNNING":
        blockers.append("OPERATIONS_STATUS_NOT_RUNNING")
    if status.get("data_feed") != "CURRENT":
        blockers.append("OPERATIONS_DATA_FEED_NOT_CURRENT")
    timestamp = status.get("snapshot_generated_at")
    try:
        generated = datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else None
        if generated is None or generated.tzinfo is None:
            raise ValueError
        generated = generated.astimezone(timezone.utc)
    except ValueError:
        blockers.append("OPERATIONS_SNAPSHOT_TIMESTAMP_INVALID")
    else:
        if generated > observed_at + timedelta(seconds=30):
            blockers.append("OPERATIONS_SNAPSHOT_FROM_FUTURE")
        elif observed_at - generated > timedelta(minutes=15):
            blockers.append("OPERATIONS_SNAPSHOT_STALE")

    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "expected_sha": expected_sha,
        "deployed_sha": deployed_sha,
        "ready": not blockers,
        "blockers": blockers,
        "operations_status": safe_status,
        "units": units,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only HOPE AWS readiness reconciliation",
    )
    parser.add_argument("expected_sha")
    arguments = parser.parse_args(argv)
    try:
        report = inspect_aws_readiness(arguments.expected_sha)
    except (OSError, ReadinessError) as exc:
        print(json.dumps(
            {"schema_version": 1, "ready": False, "error": str(exc)},
            sort_keys=True,
        ))
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["ready"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
