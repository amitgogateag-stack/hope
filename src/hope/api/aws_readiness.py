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
from pathlib import Path
import re
import subprocess
from typing import Callable


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_STATUS_BYTES = 16_384
REQUIRED_UNITS = (
    "postgresql.service",
    "hope-dashboard-web.service",
    "hope-ops-dashboard-web.service",
    "hope-ops-dashboard-export.service",
    "hope-ops-dashboard-export.timer",
    "hope-paper.service",
)


class ReadinessError(RuntimeError):
    """The observation could not be authenticated or safely bounded."""


def _deployed_sha(root: Path) -> str:
    current = root / "current"
    if not current.is_symlink():
        raise ReadinessError("AWS_CURRENT_RELEASE_SYMLINK_REQUIRED")
    releases = (root / "releases").resolve(strict=True)
    deployed = current.resolve(strict=True)
    if deployed.parent != releases or not SHA_RE.fullmatch(deployed.name):
        raise ReadinessError("AWS_CURRENT_RELEASE_TARGET_UNTRUSTED")
    return deployed.name


def _systemd_probe(unit: str) -> dict[str, str]:
    result = subprocess.run(
        [
            "/usr/bin/systemctl", "show", unit, "--no-pager",
            "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
            "-p", "UnitFileState", "-p", "NRestarts",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise ReadinessError("AWS_SYSTEMD_OBSERVATION_FAILED:" + unit)
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        allowed = {
            "LoadState", "ActiveState", "SubState", "UnitFileState", "NRestarts",
        }
        if separator and key in allowed:
            values[key] = value
    if "LoadState" not in values or "ActiveState" not in values:
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
    systemd_probe: Callable[[str], dict[str, str]] = _systemd_probe,
    status_probe: Callable[[str], dict[str, object]] = _status_probe,
    status_url: str = "http://127.0.0.1:8788/status.json",
) -> dict[str, object]:
    """Return a sanitized, deterministic readiness report without host writes."""
    if not SHA_RE.fullmatch(expected_sha):
        raise ReadinessError("AWS_EXPECTED_SHA_REQUIRED")
    if status_url != "http://127.0.0.1:8788/status.json":
        raise ReadinessError("AWS_STATUS_URL_NOT_APPROVED")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    deployed_sha = _deployed_sha(root)
    blockers: list[str] = []
    if deployed_sha != expected_sha:
        blockers.append("DEPLOYED_SHA_MISMATCH")

    units: dict[str, dict[str, str]] = {}
    for unit in REQUIRED_UNITS:
        values = systemd_probe(unit)
        units[unit] = values
        if values["LoadState"] != "loaded":
            blockers.append("UNIT_NOT_LOADED:" + unit)
    active_units = (
        "postgresql.service",
        "hope-dashboard-web.service",
        "hope-ops-dashboard-web.service",
    )
    for unit in active_units:
        if units[unit]["ActiveState"] != "active":
            blockers.append("UNIT_NOT_ACTIVE:" + unit)
    for unit in ("hope-ops-dashboard-export.timer", "hope-paper.service"):
        if units[unit]["LoadState"] == "loaded" and units[unit]["ActiveState"] != "active":
            blockers.append("UNIT_NOT_ACTIVE:" + unit)

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
