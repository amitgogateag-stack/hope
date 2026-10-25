from datetime import datetime, timezone
import os
from pathlib import Path

import pytest

from hope.api import aws_readiness
from hope.api import operations_deployment
from hope.api.aws_readiness import (
    REQUIRED_UNITS, SERVICE_RECOVERY_POLICIES, ReadinessError,
    inspect_aws_readiness,
)
from hope.api.operations_deployment import DeploymentError


EXPECTED = "b" * 40
NOW = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)


def _release_ok(_release, _sha):
    return {"status": "COMPATIBLE_CODE"}


def _root(tmp_path: Path, deployed: str = EXPECTED) -> Path:
    root = tmp_path / "hope-operations"
    release = root / "releases" / deployed
    release.mkdir(parents=True)
    (root / "current").symlink_to(release)
    return root


def _units(*, missing: str | None = None):
    def probe(unit: str) -> dict[str, str]:
        assert unit in REQUIRED_UNITS
        if unit == missing:
            values = {
                "LoadState": "not-found", "ActiveState": "inactive",
                "SubState": "dead", "UnitFileState": "", "NRestarts": "0",
            }
        else:
            values = {
                "LoadState": "loaded", "ActiveState": "active",
                "SubState": "running",
                "UnitFileState": (
                    "static" if unit == "hope-ops-dashboard-export.service"
                    else "enabled"
                ),
                "NRestarts": "0",
            }
        return {**values, **SERVICE_RECOVERY_POLICIES.get(unit, {})}
    return probe


def _inactive(unit_name: str):
    base = _units()

    def probe(unit: str) -> dict[str, str]:
        values = base(unit)
        if unit == unit_name:
            values = {**values, "ActiveState": "inactive", "SubState": "dead"}
        return values

    return probe


def _status(timestamp: str = "2026-10-24T22:55:00+00:00"):
    return lambda _: {
        "schema_version": 1,
        "service": "RUNNING",
        "data_feed": "CURRENT",
        "snapshot_generated_at": timestamp,
        "secret": "must-not-be-reported",
    }


def test_exact_release_current_status_and_loaded_units_are_ready(tmp_path):
    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_units(), status_probe=_status(), release_probe=_release_ok,
    )
    assert report["ready"] is True
    assert report["blockers"] == []
    assert "secret" not in report["operations_status"]


def test_observed_aws_drift_is_reported_without_mutating_release(tmp_path):
    deployed = "f17a54929a8ecb7745a62c118893b55494aeb2f1"
    root = _root(tmp_path, deployed)
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    report = inspect_aws_readiness(
        EXPECTED, root=root, now=NOW,
        systemd_probe=_units(missing="hope-paper.service"),
        release_probe=_release_ok,
        status_probe=lambda _: {
            "schema_version": 1,
            "service": "RUNNING",
            "data_feed": "STALE",
            "snapshot_generated_at": "2026-10-17T18:35:14.543100+00:00",
        },
    )
    assert report["ready"] is False
    assert report["deployed_sha"] == deployed
    assert report["blockers"] == [
        "DEPLOYED_SHA_MISMATCH",
        "UNIT_NOT_LOADED:hope-paper.service",
        "OPERATIONS_DATA_FEED_NOT_CURRENT",
        "OPERATIONS_SNAPSHOT_STALE",
    ]
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == before


@pytest.mark.parametrize("target", ["outside", "not-a-sha"])
def test_current_symlink_must_name_one_direct_release_child(tmp_path, target):
    root = tmp_path / "hope-operations"
    releases = root / "releases"
    releases.mkdir(parents=True)
    destination = tmp_path / target if target == "outside" else releases / target
    destination.mkdir()
    (root / "current").symlink_to(destination)
    with pytest.raises(ReadinessError, match="^AWS_CURRENT_RELEASE_TARGET_UNTRUSTED$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW,
            systemd_probe=_units(), status_probe=_status(), release_probe=_release_ok,
        )


@pytest.mark.parametrize(
    ("timestamp", "blocker"),
    [
        ("not-a-time", "OPERATIONS_SNAPSHOT_TIMESTAMP_INVALID"),
        ("2026-10-24T23:01:00+00:00", "OPERATIONS_SNAPSHOT_FROM_FUTURE"),
        ("2026-10-24T22:44:59+00:00", "OPERATIONS_SNAPSHOT_STALE"),
    ],
)
def test_snapshot_time_is_bounded_and_timezone_aware(tmp_path, timestamp, blocker):
    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_units(), status_probe=_status(timestamp),
        release_probe=_release_ok,
    )
    assert blocker in report["blockers"]


def test_status_probe_is_pinned_to_private_operations_endpoint(tmp_path):
    with pytest.raises(ReadinessError, match="^AWS_STATUS_URL_NOT_APPROVED$"):
        inspect_aws_readiness(
            EXPECTED, root=_root(tmp_path), now=NOW,
            systemd_probe=_units(), status_probe=_status(), release_probe=_release_ok,
            status_url="http://example.invalid/status.json",
        )


@pytest.mark.parametrize(
    "unit", ["hope-ops-dashboard-export.timer", "hope-paper.service"],
)
def test_autonomous_units_must_be_active_for_operational_readiness(tmp_path, unit):
    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_inactive(unit), status_probe=_status(),
        release_probe=_release_ok,
    )
    assert "UNIT_NOT_ACTIVE:" + unit in report["blockers"]


@pytest.mark.parametrize("unit", (
    "postgresql.service",
    "hope-dashboard-web.service",
    "hope-ops-dashboard-web.service",
    "hope-ops-dashboard-export.timer",
    "hope-paper.service",
))
def test_transiently_active_but_disabled_unit_cannot_claim_reboot_readiness(
    tmp_path, unit,
):
    base = _units()

    def disabled_probe(observed_unit):
        values = base(observed_unit)
        if observed_unit == unit:
            values = {**values, "UnitFileState": "disabled"}
        return values

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=disabled_probe, status_probe=_status(),
        release_probe=_release_ok,
    )
    assert "UNIT_NOT_ENABLED:" + unit in report["blockers"]


@pytest.mark.parametrize("unit", (
    "postgresql.service",
    "hope-dashboard-web.service",
    "hope-ops-dashboard-web.service",
    "hope-paper.service",
))
def test_active_unit_without_running_process_cannot_claim_readiness(tmp_path, unit):
    base = _units()

    def exited_probe(observed_unit):
        values = base(observed_unit)
        if observed_unit == unit:
            values = {**values, "SubState": "exited"}
        return values

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=exited_probe, status_probe=_status(),
        release_probe=_release_ok,
    )
    assert "UNIT_NOT_RUNNING:" + unit in report["blockers"]


@pytest.mark.parametrize(("unit", "property_name", "unsafe_value"), (
    ("hope-dashboard-web.service", "Type", "oneshot"),
    ("hope-dashboard-web.service", "Restart", "no"),
    ("hope-dashboard-web.service", "RestartUSec", "0"),
    ("hope-ops-dashboard-web.service", "Type", "oneshot"),
    ("hope-ops-dashboard-web.service", "Restart", "no"),
    ("hope-ops-dashboard-web.service", "RestartUSec", "0"),
    ("hope-paper.service", "Type", "simple"),
    ("hope-paper.service", "Restart", "no"),
    ("hope-paper.service", "RestartUSec", "0"),
    ("hope-paper.service", "WatchdogUSec", "0"),
))
def test_running_service_without_reviewed_crash_recovery_cannot_claim_readiness(
    tmp_path, unit, property_name, unsafe_value,
):
    base = _units()

    def nonrecovering_probe(observed_unit):
        values = base(observed_unit)
        if observed_unit == unit:
            values = {**values, property_name: unsafe_value}
        return values

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=nonrecovering_probe, status_probe=_status(),
        release_probe=_release_ok,
    )
    assert "UNIT_RECOVERY_POLICY_MISMATCH:" + unit in report["blockers"]


def test_recovery_policies_match_the_reviewed_systemd_units():
    repository = Path(__file__).parents[2]
    paths = {
        "hope-dashboard-web.service":
            repository / "deploy/dashboard/hope-dashboard-web.service",
        "hope-ops-dashboard-web.service":
            repository / "deploy/operations/hope-ops-dashboard-web.service",
        "hope-paper.service":
            repository / "deploy/operations/hope-paper.service",
    }
    for unit, expected in SERVICE_RECOVERY_POLICIES.items():
        configured = {}
        for line in paths[unit].read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"Type", "Restart", "RestartSec", "WatchdogSec"}:
                configured[key] = value
        observed = {
            "Type": configured.get("Type"),
            "Restart": configured.get("Restart"),
            "RestartUSec": configured.get("RestartSec"),
        }
        if "WatchdogUSec" in expected:
            observed["WatchdogUSec"] = configured.get("WatchdogSec")
        assert observed == expected


def test_release_directory_name_cannot_substitute_for_authenticated_git_head(tmp_path):
    observed = []

    def reject_release(release: Path, expected_sha: str):
        observed.append((release, expected_sha))
        raise DeploymentError("OPERATIONS_RELEASE_HEAD_MISMATCH")

    root = _root(tmp_path)
    with pytest.raises(ReadinessError, match="^AWS_DEPLOYED_RELEASE_UNVERIFIED$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW,
            systemd_probe=_units(), status_probe=_status(),
            release_probe=reject_release,
        )
    assert observed == [((root / "releases" / EXPECTED).resolve(), EXPECTED)]


def test_release_authentication_uses_deployed_sha_not_requested_target(tmp_path):
    deployed = "a" * 40
    observed = []

    def record_release(release: Path, expected_sha: str):
        observed.append((release, expected_sha))
        return {"status": "COMPATIBLE_CODE"}

    root = _root(tmp_path, deployed)
    report = inspect_aws_readiness(
        EXPECTED, root=root, now=NOW,
        systemd_probe=_units(), status_probe=_status(),
        release_probe=record_release,
    )
    assert report["blockers"][0] == "DEPLOYED_SHA_MISMATCH"
    assert observed == [((root / "releases" / deployed).resolve(), deployed)]


def test_naive_observation_time_is_rejected(tmp_path):
    with pytest.raises(ReadinessError, match="^AWS_OBSERVATION_TIME_INVALID$"):
        inspect_aws_readiness(
            EXPECTED, root=_root(tmp_path), now=NOW.replace(tzinfo=None),
            systemd_probe=_units(), status_probe=_status(),
            release_probe=_release_ok,
        )


def test_systemd_timeout_is_sanitized():
    def timeout(*_args, **_kwargs):
        raise RuntimeError("DIAGNOSTIC_TIMEOUT")

    with pytest.raises(
        ReadinessError,
        match="^AWS_SYSTEMD_OBSERVATION_TIMEOUT:hope-paper.service$",
    ):
        aws_readiness._systemd_probe("hope-paper.service", capture=timeout)


def test_systemd_probe_uses_explicit_output_and_time_bounds():
    observed = []
    policy = SERVICE_RECOVERY_POLICIES["hope-paper.service"]
    stdout = "\n".join((
        "LoadState=loaded", "ActiveState=active", "SubState=running",
        "UnitFileState=enabled", "NRestarts=0",
        *(key + "=" + value for key, value in policy.items()),
    )) + "\n"

    def capture(command, **kwargs):
        observed.append((command, kwargs))
        return stdout

    assert aws_readiness._systemd_probe(
        "hope-paper.service", capture=capture,
    )["Restart"] == "always"
    _command, kwargs = observed[0]
    assert kwargs == {
        "timeout": 5,
        "max_output": aws_readiness.MAX_SYSTEMD_BYTES,
        "env": {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        },
    }


@pytest.mark.parametrize(("failure", "error"), (
    (
        RuntimeError("DIAGNOSTIC_OUTPUT_LIMIT"),
        "AWS_SYSTEMD_OBSERVATION_TOO_LARGE:hope-paper.service",
    ),
    (
        RuntimeError("DIAGNOSTIC_POSIX_REQUIRED"),
        "AWS_SYSTEMD_OBSERVATION_FAILED:hope-paper.service",
    ),
    (
        OSError("private host detail"),
        "AWS_SYSTEMD_OBSERVATION_FAILED:hope-paper.service",
    ),
    (
        aws_readiness.subprocess.CalledProcessError(1, ["systemctl"]),
        "AWS_SYSTEMD_OBSERVATION_FAILED:hope-paper.service",
    ),
    (
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
        "AWS_SYSTEMD_OBSERVATION_INVALID:hope-paper.service",
    ),
))
def test_systemd_capture_failures_are_sanitized(failure, error):
    def fail(*_args, **_kwargs):
        raise failure

    with pytest.raises(ReadinessError, match="^" + error + "$"):
        aws_readiness._systemd_probe("hope-paper.service", capture=fail)


def test_systemd_probe_collects_effective_paper_recovery_policy():
    observed = []
    policy = SERVICE_RECOVERY_POLICIES["hope-paper.service"]
    stdout = "\n".join((
        "LoadState=loaded", "ActiveState=active", "SubState=running",
        "UnitFileState=enabled", "NRestarts=0",
        *(key + "=" + value for key, value in policy.items()),
    )) + "\n"

    def run(command, **kwargs):
        observed.append((command, kwargs))
        return stdout

    assert aws_readiness._systemd_probe("hope-paper.service", capture=run) == {
        "LoadState": "loaded", "ActiveState": "active", "SubState": "running",
        "UnitFileState": "enabled", "NRestarts": "0", **policy,
    }
    command, kwargs = observed[0]
    assert command[:4] == [
        "/usr/bin/systemctl", "show", "hope-paper.service", "--no-pager",
    ]
    assert command.count("-p") == 9
    assert kwargs["timeout"] == 5
    assert kwargs["env"]["LC_ALL"] == "C"


@pytest.mark.parametrize("stdout", [
    "LoadState=loaded\nActiveState=active\n",
    (
        "LoadState=loaded\nActiveState=active\nSubState=running\n"
        "UnitFileState=enabled\nNRestarts=not-a-number\n"
    ),
    (
        "LoadState=loaded\nActiveState=active\nSubState=running\n"
        "UnitFileState=enabled\nNRestarts=123456789012345678901\n"
    ),
])
def test_systemd_observation_requires_complete_bounded_state(stdout):
    with pytest.raises(
        ReadinessError,
        match="^AWS_SYSTEMD_OBSERVATION_INVALID:hope-paper.service$",
    ):
        aws_readiness._systemd_probe(
            "hope-paper.service", capture=lambda *_args, **_kwargs: stdout,
        )


@pytest.mark.skipif(os.name != "posix", reason="Linux flock contract")
def test_readiness_holds_shared_activation_fence_across_every_probe(tmp_path):
    root = _root(tmp_path)
    observations = []

    def assert_mutation_excluded(stage):
        observations.append(stage)
        with pytest.raises(
            DeploymentError, match="^OPERATIONS_ACTIVATION_LOCKED$",
        ):
            with operations_deployment._activation_guard(root):
                pytest.fail("exclusive activation entered a readiness snapshot")

    def release_probe(*_args):
        assert_mutation_excluded("release")
        return {"status": "COMPATIBLE_CODE"}

    def systemd_probe(unit):
        assert_mutation_excluded("systemd:" + unit)
        return _units()(unit)

    def status_probe(_url):
        assert_mutation_excluded("status")
        return _status()(_url)

    report = inspect_aws_readiness(
        EXPECTED, root=root, now=NOW, systemd_probe=systemd_probe,
        status_probe=status_probe, release_probe=release_probe,
    )
    assert report["ready"] is True
    assert observations == [
        "release", *("systemd:" + unit for unit in REQUIRED_UNITS), "status",
    ]


@pytest.mark.skipif(os.name != "posix", reason="Linux flock contract")
def test_active_deployment_change_blocks_readiness_before_any_probe(tmp_path):
    root = _root(tmp_path)
    observed = []

    def forbidden(*_args):
        observed.append(True)
        pytest.fail("locked activation must block the complete observation")

    with operations_deployment._activation_guard(root):
        with pytest.raises(
            ReadinessError, match="^AWS_ACTIVATION_STATE_UNAVAILABLE$",
        ):
            inspect_aws_readiness(
                EXPECTED, root=root, now=NOW,
                systemd_probe=forbidden, status_probe=forbidden,
                release_probe=forbidden,
            )
    assert observed == []
