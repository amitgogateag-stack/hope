"""Read-only AWS host reconciliation for a reviewed HOPE release.

This module deliberately observes only public filesystem metadata, fixed systemd
properties, and the bounded loopback operations status document.  It never
loads environment files, imports a PAPER factory, or changes host state.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Callable

from hope.api import operations_deployment
from hope.api.diagnostic_io import bounded_capture
from hope.api.operations_deployment import DeploymentError, inspect_release


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_STATUS_BYTES = 16_384
MAX_DASHBOARD_RELEASE_BYTES = 1_024
MAX_SYSTEMD_BYTES = 4_096
MAX_UNIT_BYTES = 16_384
LOOPBACK_OBSERVATION_TIMEOUT = 5.0
STATUS_OBSERVATION_TIMEOUT = 5.0
SYSTEMD_OBSERVATION_TIMEOUT = 5.0
SYSTEMD_REVALIDATION_TIMEOUT = 5.0
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
HOPE_UNIT_FRAGMENTS = {
    unit: "/etc/systemd/system/" + unit
    for unit in REQUIRED_UNITS
    if unit != "postgresql.service"
}
HOPE_UNIT_TEMPLATES = {
    unit: (
        Path("deploy/dashboard/hope-dashboard-web.service")
        if unit == "hope-dashboard-web.service"
        else Path("deploy/operations") / unit
    )
    for unit in HOPE_UNIT_FRAGMENTS
}


class ReadinessError(RuntimeError):
    """The observation could not be authenticated or safely bounded."""


def _unit_file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns, info.st_uid, info.st_gid,
    )


def _unit_directory_identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _open_direct_unit_path(
    path: Path,
) -> tuple[list[int], tuple[tuple[int, ...], ...]]:
    """Open every path component without following an intermediate link."""
    if not path.is_absolute():
        raise OSError("absolute unit path required")
    components = path.parts[1:]
    if not components or any(part in {"", ".", ".."} for part in components):
        raise OSError("invalid unit path")
    nofollow = os.O_NOFOLLOW
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | nofollow | close_on_exec
    file_flags = os.O_RDONLY | os.O_NONBLOCK | nofollow | close_on_exec
    descriptors: list[int] = []
    identities: list[tuple[int, ...]] = []
    try:
        descriptor = os.open("/", directory_flags)
        descriptors.append(descriptor)
        identities.append(_unit_directory_identity(os.fstat(descriptor)))
        for component in components[:-1]:
            descriptor = os.open(
                component, directory_flags, dir_fd=descriptors[-1],
            )
            descriptors.append(descriptor)
            identities.append(_unit_directory_identity(os.fstat(descriptor)))
        descriptor = os.open(
            components[-1], file_flags, dir_fd=descriptors[-1],
        )
        descriptors.append(descriptor)
        identities.append(_unit_file_identity(os.fstat(descriptor)))
        return descriptors, tuple(identities)
    except BaseException:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _close_unit_path(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)


def _validate_open_unit_path(
    path: Path,
    descriptors: list[int],
    expected: tuple[tuple[int, ...], ...],
) -> None:
    components = path.parts[1:]
    try:
        root = os.stat("/", follow_symlinks=False)
        if _unit_directory_identity(root) != expected[0]:
            raise ReadinessError("AWS_UNIT_CONFIGURATION_CHANGED")
        for index, component in enumerate(components):
            observed = os.stat(
                component, dir_fd=descriptors[index], follow_symlinks=False,
            )
            identity = (
                _unit_file_identity(observed)
                if index == len(components) - 1
                else _unit_directory_identity(observed)
            )
            if identity != expected[index + 1]:
                raise ReadinessError("AWS_UNIT_CONFIGURATION_CHANGED")
    except ReadinessError:
        raise
    except OSError as exc:
        raise ReadinessError("AWS_UNIT_CONFIGURATION_CHANGED") from exc


def _bounded_unit_file_snapshot(
    path: Path,
) -> tuple[bytes, tuple[tuple[int, ...], ...]]:
    """Read one stable file and bind the descriptor to its current pathname."""
    try:
        descriptors, path_identity = _open_direct_unit_path(path)
    except OSError as exc:
        raise ReadinessError("AWS_UNIT_CONFIGURATION_UNAVAILABLE") from exc
    descriptor = descriptors[-1]
    try:
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_mode & 0o022
                    or before.st_nlink != 1 or before.st_size > MAX_UNIT_BYTES):
                raise ReadinessError("AWS_UNIT_CONFIGURATION_UNTRUSTED")
            chunks: list[bytes] = []
            remaining = MAX_UNIT_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
        except ReadinessError:
            raise
        except OSError as exc:
            raise ReadinessError("AWS_UNIT_CONFIGURATION_UNAVAILABLE") from exc
        before_identity = _unit_file_identity(before)
        after_identity = _unit_file_identity(after)
        if before_identity != after_identity or len(content) != before.st_size:
            raise ReadinessError("AWS_UNIT_CONFIGURATION_CHANGED")
        if len(content) > MAX_UNIT_BYTES:
            raise ReadinessError("AWS_UNIT_CONFIGURATION_UNTRUSTED")
        if path_identity[-1] != after_identity:
            raise ReadinessError("AWS_UNIT_CONFIGURATION_CHANGED")
        _validate_open_unit_path(path, descriptors, path_identity)
        return content, path_identity
    finally:
        _close_unit_path(descriptors)


def _bounded_unit_file(path: Path) -> bytes:
    return _bounded_unit_file_snapshot(path)[0]


def _require_same_unit_file(
    path: Path, expected: tuple[tuple[int, ...], ...],
) -> None:
    try:
        descriptors, observed = _open_direct_unit_path(path)
    except OSError as exc:
        raise ReadinessError("AWS_UNIT_CONFIGURATION_CHANGED") from exc
    try:
        _validate_open_unit_path(path, descriptors, observed)
        if observed != expected:
            raise ReadinessError("AWS_UNIT_CONFIGURATION_CHANGED")
    finally:
        _close_unit_path(descriptors)


def _require_trusted_unit_storage(
    path: Path,
    identity: tuple[tuple[int, ...], ...],
    *,
    trust_root: Path,
    required_owner: int | None = None,
) -> None:
    """Require a non-writable, single-owner chain below an authenticated root."""
    if not path.is_absolute() or not trust_root.is_absolute():
        raise ReadinessError("AWS_UNIT_CONFIGURATION_UNTRUSTED")
    try:
        path.relative_to(trust_root)
    except ValueError as exc:
        raise ReadinessError("AWS_UNIT_CONFIGURATION_UNTRUSTED") from exc
    root_index = len(trust_root.parts) - 1
    if len(identity) != len(path.parts) or root_index >= len(identity) - 1:
        raise ReadinessError("AWS_UNIT_CONFIGURATION_UNTRUSTED")
    root_owner = identity[root_index][3]
    owner = root_owner if required_owner is None else required_owner
    for directory in identity[root_index:-1]:
        if (not stat.S_ISDIR(directory[2]) or directory[2] & 0o022
                or directory[3] != owner):
            raise ReadinessError("AWS_UNIT_CONFIGURATION_UNTRUSTED")
    if identity[-1][7] != owner:
        raise ReadinessError("AWS_UNIT_CONFIGURATION_UNTRUSTED")


def _unit_file_matches(
    installed: Path,
    reviewed: Path,
    *,
    installed_root: Path | None = None,
    reviewed_root: Path | None = None,
    attestations: list[
        tuple[Path, tuple[tuple[int, ...], ...]]
    ] | None = None,
) -> bool:
    installed_content, installed_identity = _bounded_unit_file_snapshot(installed)
    reviewed_content, reviewed_identity = _bounded_unit_file_snapshot(reviewed)
    installed_root = installed.parent if installed_root is None else installed_root
    reviewed_root = reviewed.parent if reviewed_root is None else reviewed_root
    _require_trusted_unit_storage(
        installed, installed_identity, trust_root=installed_root,
        required_owner=0 if installed_root == Path("/") else None,
    )
    _require_trusted_unit_storage(
        reviewed, reviewed_identity, trust_root=reviewed_root,
    )
    # Reading the second file creates a comparison window in which the first
    # pathname could otherwise be exchanged after its descriptor was closed.
    # Revalidate both names only after both immutable snapshots exist.
    _require_same_unit_file(installed, installed_identity)
    _require_same_unit_file(reviewed, reviewed_identity)
    if attestations is not None:
        attestations.extend((
            (installed, installed_identity),
            (reviewed, reviewed_identity),
        ))
    return installed_content == reviewed_content


def _deployment_root_identity(root: Path) -> tuple[Path, tuple[int, int]]:
    try:
        named_info = root.lstat()
    except OSError as exc:
        raise ReadinessError("AWS_DEPLOYMENT_ROOT_UNAVAILABLE") from exc
    if not stat.S_ISDIR(named_info.st_mode) or named_info.st_mode & 0o022:
        raise ReadinessError("AWS_DEPLOYMENT_ROOT_UNTRUSTED")
    named_identity = (named_info.st_dev, named_info.st_ino)
    try:
        resolved = root.resolve(strict=True)
        info = resolved.lstat()
    except OSError as exc:
        raise ReadinessError("AWS_DEPLOYMENT_ROOT_UNAVAILABLE") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ReadinessError("AWS_DEPLOYMENT_ROOT_UNTRUSTED")
    if (info.st_dev, info.st_ino) != named_identity:
        raise ReadinessError("AWS_DEPLOYMENT_ROOT_CHANGED")
    return resolved, named_identity


def _require_same_deployment_root(
    root: Path, expected_identity: tuple[int, int],
) -> None:
    try:
        info = root.lstat()
    except OSError as exc:
        raise ReadinessError("AWS_DEPLOYMENT_ROOT_CHANGED") from exc
    if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022
            or (info.st_dev, info.st_ino) != expected_identity):
        raise ReadinessError("AWS_DEPLOYMENT_ROOT_CHANGED")


def _trusted_release_directory(path: Path) -> tuple[int, int]:
    """Reject redirected or group/world-writable release storage."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReadinessError("AWS_RELEASE_STORAGE_UNAVAILABLE") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ReadinessError("AWS_RELEASE_STORAGE_UNTRUSTED")
    return info.st_dev, info.st_ino


def _deployed_release(
    root: Path,
) -> tuple[Path, str, tuple[int, int], tuple[int, int]]:
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise ReadinessError("AWS_RELEASE_STORAGE_UNAVAILABLE") from exc
    releases = root / "releases"
    releases_identity = _trusted_release_directory(releases)
    current = root / "current"
    if not current.is_symlink():
        raise ReadinessError("AWS_CURRENT_RELEASE_SYMLINK_REQUIRED")
    try:
        deployed = current.resolve(strict=True)
    except OSError as exc:
        raise ReadinessError("AWS_CURRENT_RELEASE_TARGET_UNAVAILABLE") from exc
    if deployed.parent != releases or not SHA_RE.fullmatch(deployed.name):
        raise ReadinessError("AWS_CURRENT_RELEASE_TARGET_UNTRUSTED")
    deployed_identity = _trusted_release_directory(deployed)
    return deployed, deployed.name, releases_identity, deployed_identity


def _require_same_release_storage(
    root: Path,
    expected: tuple[Path, str, tuple[int, int], tuple[int, int]],
) -> None:
    try:
        observed = _deployed_release(root)
    except ReadinessError as exc:
        raise ReadinessError("AWS_RELEASE_STORAGE_CHANGED") from exc
    if observed != expected:
        raise ReadinessError("AWS_RELEASE_STORAGE_CHANGED")


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
        "FragmentPath", "DropInPaths", "NeedDaemonReload", "MainPID",
        "InvocationID", "ExecMainStartTimestampMonotonic",
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
            or not re.fullmatch(r"[0-9]{1,20}", values["NRestarts"])
            or not re.fullmatch(r"[0-9]{1,20}", values["MainPID"])
            or not re.fullmatch(
                r"[0-9]{1,20}", values["ExecMainStartTimestampMonotonic"],
            )
            or not re.fullmatch(r"(?:|[0-9a-f]{32})", values["InvocationID"])):
        raise ReadinessError("AWS_SYSTEMD_OBSERVATION_INVALID:" + unit)
    return values


def _loopback_json_probe(
    url: str,
    *,
    approved_url: str,
    max_bytes: int,
    error_scope: str,
    timeout: float,
    capture: Callable[..., str] = bounded_capture,
) -> dict[str, object]:
    if url != approved_url:
        raise ReadinessError("AWS_" + error_scope + "_URL_NOT_APPROVED")
    if (not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ReadinessError("AWS_" + error_scope + "_OBSERVATION_TIMEOUT")
    timeout = min(float(timeout), LOOPBACK_OBSERVATION_TIMEOUT)
    command = [
        "/usr/bin/curl", "-q", "--silent", "--show-error",
        "--noproxy", "*", "--proto", "=http",
        "--max-time", f"{timeout:g}",
        "--request", "GET", "--header", "Host: 127.0.0.1",
        "--write-out", "\n%{http_code}", url,
    ]
    try:
        output = capture(
            command,
            timeout=timeout,
            max_output=max_bytes + 4,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except RuntimeError as exc:
        if str(exc) == "DIAGNOSTIC_TIMEOUT":
            raise ReadinessError(
                "AWS_" + error_scope + "_OBSERVATION_TIMEOUT",
            ) from exc
        if str(exc) == "DIAGNOSTIC_OUTPUT_LIMIT":
            raise ReadinessError(
                "AWS_" + error_scope + "_DOCUMENT_TOO_LARGE",
            ) from exc
        raise ReadinessError("AWS_" + error_scope + "_OBSERVATION_FAILED") from exc
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 28:
            raise ReadinessError(
                "AWS_" + error_scope + "_OBSERVATION_TIMEOUT",
            ) from exc
        raise ReadinessError("AWS_" + error_scope + "_OBSERVATION_FAILED") from exc
    except OSError as exc:
        raise ReadinessError("AWS_" + error_scope + "_OBSERVATION_FAILED") from exc
    except UnicodeError as exc:
        raise ReadinessError("AWS_" + error_scope + "_DOCUMENT_INVALID") from exc
    raw, separator, status = output.rpartition("\n")
    if not separator or not re.fullmatch(r"[0-9]{3}", status):
        raise ReadinessError("AWS_" + error_scope + "_DOCUMENT_INVALID")
    if status != "200":
        raise ReadinessError("AWS_" + error_scope + "_HTTP_FAILED:" + status)
    if len(raw.encode("utf-8")) > max_bytes:
        raise ReadinessError("AWS_" + error_scope + "_DOCUMENT_TOO_LARGE")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReadinessError("AWS_" + error_scope + "_DOCUMENT_INVALID") from exc
    if not isinstance(document, dict):
        raise ReadinessError("AWS_" + error_scope + "_DOCUMENT_INVALID")
    return document


def _status_probe(
    url: str,
    *,
    timeout: float = STATUS_OBSERVATION_TIMEOUT,
    capture: Callable[..., str] = bounded_capture,
) -> dict[str, object]:
    return _loopback_json_probe(
        url, approved_url="http://127.0.0.1:8788/status.json",
        max_bytes=MAX_STATUS_BYTES, error_scope="STATUS", timeout=timeout,
        capture=capture,
    )


def _dashboard_release_probe(
    url: str,
    *,
    timeout: float = LOOPBACK_OBSERVATION_TIMEOUT,
    capture: Callable[..., str] = bounded_capture,
) -> dict[str, object]:
    return _loopback_json_probe(
        url, approved_url="http://127.0.0.1:8788/release.json",
        max_bytes=MAX_DASHBOARD_RELEASE_BYTES,
        error_scope="DASHBOARD_RELEASE", timeout=timeout, capture=capture,
    )


def inspect_aws_readiness(
    expected_sha: str,
    *,
    root: Path = Path("/opt/hope-operations"),
    now: datetime | None = None,
    systemd_probe: Callable[..., dict[str, str]] = _systemd_probe,
    status_probe: Callable[..., dict[str, object]] = _status_probe,
    dashboard_release_probe: Callable[..., dict[str, object]] = _dashboard_release_probe,
    unit_file_probe: Callable[[Path, Path], bool] | None = None,
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
    root, root_identity = _deployment_root_identity(root)

    # The release pointer, unit state, and dashboard document form one
    # operational snapshot.  Every supported activation holds the exclusive
    # side of this fence, so a readiness report must hold the shared side from
    # release authentication through the final status observation.  Otherwise
    # it could combine a release from before a switch with services or status
    # from after it and incorrectly report READY.
    try:
        with operations_deployment._activation_guard(root, shared=True):
            _require_same_deployment_root(root, root_identity)
            report = _inspect_stable_readiness(
                expected_sha, root=root, observed_at=observed_at,
                systemd_probe=systemd_probe, status_probe=status_probe,
                dashboard_release_probe=dashboard_release_probe,
                unit_file_probe=unit_file_probe,
                release_probe=release_probe, status_url=status_url,
                monotonic=monotonic,
            )
            _require_same_deployment_root(root, root_identity)
            return report
    except DeploymentError as exc:
        raise ReadinessError("AWS_ACTIVATION_STATE_UNAVAILABLE") from exc


def _inspect_stable_readiness(
    expected_sha: str,
    *,
    root: Path,
    observed_at: datetime,
    systemd_probe: Callable[..., dict[str, str]],
    status_probe: Callable[..., dict[str, object]],
    dashboard_release_probe: Callable[..., dict[str, object]],
    unit_file_probe: Callable[[Path, Path], bool] | None,
    release_probe: Callable[[Path, str], dict[str, object]],
    status_url: str,
    monotonic: Callable[[], float],
) -> dict[str, object]:
    """Observe one generation while the caller holds the shared fence."""
    release_storage = _deployed_release(root)
    (deployed_release, deployed_sha, _releases_identity,
     _deployed_identity) = release_storage
    try:
        release_probe(deployed_release, deployed_sha)
    except (OSError, DeploymentError) as exc:
        raise ReadinessError("AWS_DEPLOYED_RELEASE_UNVERIFIED") from exc
    _require_same_release_storage(root, release_storage)
    blockers: list[str] = []
    if deployed_sha != expected_sha:
        blockers.append("DEPLOYED_SHA_MISMATCH")

    units: dict[str, dict[str, str]] = {}
    unit_file_attestations: list[
        tuple[Path, tuple[tuple[int, ...], ...]]
    ] = []
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

    # Effective liveness and restart policy are insufficient when systemd loaded
    # an unexpected fragment or an unreviewed drop-in can replace ExecStart.
    # PostgreSQL remains distribution-managed; every HOPE unit is installed from
    # the reviewed operator package at one fixed system path with no overrides.
    for unit, fragment in HOPE_UNIT_FRAGMENTS.items():
        if units[unit]["LoadState"] != "loaded":
            continue
        if (units[unit]["FragmentPath"] != fragment
                or units[unit]["DropInPaths"] != ""
                or units[unit]["NeedDaemonReload"] != "no"):
            blockers.append("UNIT_CONFIGURATION_MISMATCH:" + unit)
            continue
        reviewed = deployed_release / HOPE_UNIT_TEMPLATES[unit]
        matches = (
            _unit_file_matches(
                Path(fragment), reviewed, installed_root=Path("/"),
                reviewed_root=deployed_release,
                attestations=unit_file_attestations,
            )
            if unit_file_probe is None
            else unit_file_probe(Path(fragment), reviewed)
        )
        if not matches:
            blockers.append("UNIT_CONFIGURATION_MISMATCH:" + unit)

    for unit in RUNNING_UNITS:
        if units[unit]["LoadState"] != "loaded":
            continue
        if units[unit]["ActiveState"] != "active":
            blockers.append("UNIT_NOT_ACTIVE:" + unit)
        elif units[unit]["SubState"] != "running":
            blockers.append("UNIT_NOT_RUNNING:" + unit)
        elif (not re.fullmatch(r"[1-9][0-9]{0,19}", units[unit]["MainPID"])
                or not re.fullmatch(
                    r"[0-9a-f]{32}", units[unit]["InvocationID"],
                )
                or not re.fullmatch(
                    r"[1-9][0-9]{0,19}",
                    units[unit]["ExecMainStartTimestampMonotonic"],
                )):
            blockers.append("UNIT_RUNTIME_IDENTITY_INVALID:" + unit)

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

    loopback_deadline = monotonic() + LOOPBACK_OBSERVATION_TIMEOUT
    remaining = loopback_deadline - monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise ReadinessError("AWS_DASHBOARD_RELEASE_OBSERVATION_TIMEOUT")
    dashboard_release = dashboard_release_probe(
        "http://127.0.0.1:8788/release.json", timeout=remaining,
    )
    safe_dashboard_release = {
        key: dashboard_release[key]
        for key in (
            "schema", "dashboard_commit", "paper_only", "live_enabled",
            "execution_authorized",
        )
        if key in dashboard_release
    }
    identity_keys = {
        "schema", "dashboard_commit", "paper_only", "live_enabled",
        "execution_authorized",
    }
    if (set(dashboard_release) != identity_keys
            or dashboard_release.get("schema") != "hope.dashboard-release.v1"
            or not isinstance(dashboard_release.get("dashboard_commit"), str)
            or not SHA_RE.fullmatch(dashboard_release["dashboard_commit"])):
        blockers.append("DASHBOARD_RELEASE_IDENTITY_INVALID")
    elif dashboard_release["dashboard_commit"] != deployed_sha:
        blockers.append("DASHBOARD_RELEASE_MISMATCH")
    if (dashboard_release.get("paper_only") is not True
            or dashboard_release.get("live_enabled") is not False
            or dashboard_release.get("execution_authorized") is not False):
        blockers.append("DASHBOARD_SAFETY_CONTRACT_MISMATCH")

    remaining = loopback_deadline - monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise ReadinessError("AWS_STATUS_OBSERVATION_TIMEOUT")
    status = status_probe(status_url, timeout=remaining)
    safe_status = {
        key: status[key]
        for key in ("schema_version", "service", "data_feed", "snapshot_generated_at")
        if key in status
    }
    if (type(status.get("schema_version")) is not int
            or status.get("schema_version") != 1
            or status.get("service") != "RUNNING"):
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

    # The HTTP documents are meaningful only for the service generation that
    # was authenticated before they were read. A restart does not take the
    # deployment activation fence, so re-observe every required long-running
    # unit after the loopback probes and reject a mixed-generation snapshot.
    # This is a separate, bounded deadline: the first systemd pass cannot
    # consume the time reserved for closing the observation window.
    revalidation_deadline = monotonic() + SYSTEMD_REVALIDATION_TIMEOUT
    for unit in REQUIRED_UNITS:
        initial = units[unit]
        remaining = revalidation_deadline - monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            raise ReadinessError("AWS_SYSTEMD_REVALIDATION_TIMEOUT:" + unit)
        current = systemd_probe(unit, timeout=remaining)
        if unit in RUNNING_UNITS:
            initial_identity = (
                initial["ActiveState"], initial["SubState"],
                initial["MainPID"], initial["InvocationID"],
                initial["ExecMainStartTimestampMonotonic"],
            )
            current_identity = (
                current["ActiveState"], current["SubState"],
                current["MainPID"], current["InvocationID"],
                current["ExecMainStartTimestampMonotonic"],
            )
            if current_identity != initial_identity:
                raise ReadinessError("AWS_SYSTEMD_RUNTIME_CHANGED:" + unit)
        if current != initial:
            raise ReadinessError("AWS_SYSTEMD_STATE_CHANGED:" + unit)

    # systemd's reload indicator is useful but is not a pathname identity
    # attestation. Rebind every installed/reviewed unit name to the exact files
    # compared before HTTP so a same-content or same-metadata exchange during
    # the observation window cannot claim readiness.
    for path, identity in unit_file_attestations:
        _require_same_unit_file(path, identity)

    _require_same_release_storage(root, release_storage)
    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "expected_sha": expected_sha,
        "deployed_sha": deployed_sha,
        "ready": not blockers,
        "blockers": blockers,
        "dashboard_release": safe_dashboard_release,
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
