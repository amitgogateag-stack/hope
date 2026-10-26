from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat

import pytest

from hope.api import aws_readiness
from hope.api import operations_deployment
from hope.api.aws_readiness import (
    HOPE_UNIT_FRAGMENTS, HOPE_UNIT_TEMPLATES, REQUIRED_UNITS, RUNNING_UNITS,
    SERVICE_RECOVERY_POLICIES,
    ReadinessError,
)
from hope.api.operations_deployment import DeploymentError


EXPECTED = "b" * 40
NOW = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)
INVOCATION_ID = "1" * 32


def _release_ok(_release, _sha):
    return {"status": "COMPATIBLE_CODE"}


def _root(tmp_path: Path, deployed: str = EXPECTED) -> Path:
    root = tmp_path / "hope-operations"
    release = root / "releases" / deployed
    release.mkdir(parents=True)
    (root / "current").symlink_to(release)
    return root


def _units(*, missing: str | None = None):
    def probe(unit: str, *, timeout: float = 5) -> dict[str, str]:
        assert unit in REQUIRED_UNITS
        assert 0 < timeout <= 5
        if unit == missing:
            values = {
                "LoadState": "not-found", "ActiveState": "inactive",
                "SubState": "dead", "UnitFileState": "", "NRestarts": "0",
                "FragmentPath": "", "DropInPaths": "", "NeedDaemonReload": "no",
                "MainPID": "0", "InvocationID": "",
                "ExecMainStartTimestampMonotonic": "0",
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
                "MainPID": "4321", "InvocationID": INVOCATION_ID,
                "ExecMainStartTimestampMonotonic": "123456789",
                "FragmentPath": HOPE_UNIT_FRAGMENTS.get(
                    unit, "/usr/lib/systemd/system/postgresql.service",
                ),
                "DropInPaths": "",
                "NeedDaemonReload": "no",
            }
        return {**values, **SERVICE_RECOVERY_POLICIES.get(unit, {})}
    return probe


def _inactive(unit_name: str):
    base = _units()

    def probe(unit: str, *, timeout: float = 5) -> dict[str, str]:
        values = base(unit, timeout=timeout)
        if unit == unit_name:
            values = {**values, "ActiveState": "inactive", "SubState": "dead"}
        return values

    return probe


def _status(timestamp: str = "2026-10-24T22:55:00+00:00"):
    def probe(_url, *, timeout=5):
        assert 0 < timeout <= 5
        return {
            "schema_version": 1,
            "service": "RUNNING",
            "data_feed": "CURRENT",
            "snapshot_generated_at": timestamp,
            "secret": "must-not-be-reported",
        }
    return probe


def _dashboard_release(commit: str = EXPECTED, **changes):
    value = {
        "schema": "hope.dashboard-release.v1",
        "dashboard_commit": commit,
        "paper_only": True,
        "live_enabled": False,
        "execution_authorized": False,
        **changes,
    }
    return lambda _url, *, timeout=5: value


def inspect_aws_readiness(*args, **kwargs):
    kwargs.setdefault("dashboard_release_probe", _dashboard_release())
    kwargs.setdefault("unit_file_probe", lambda _installed, _reviewed: True)
    return aws_readiness.inspect_aws_readiness(*args, **kwargs)


def test_exact_release_current_status_and_loaded_units_are_ready(tmp_path):
    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_units(), status_probe=_status(), release_probe=_release_ok,
    )
    assert report["ready"] is True
    assert report["blockers"] == []
    assert "secret" not in report["operations_status"]


def test_replaced_root_after_fence_acquisition_cannot_claim_readiness(
    tmp_path, monkeypatch,
):
    root = _root(tmp_path / "original")
    replacement = _root(tmp_path / "replacement")
    (replacement / "current").unlink()
    (replacement / "current").symlink_to(Path("releases") / EXPECTED)
    retained = tmp_path / "retained-root"
    observed = []

    @contextmanager
    def replacing_guard(observed_root, *, shared=False):
        assert shared is True
        assert observed_root == root
        root.rename(retained)
        replacement.rename(root)
        yield

    def release_probe(*_args):
        observed.append(True)
        return {"status": "COMPATIBLE_CODE"}

    monkeypatch.setattr(
        operations_deployment, "_activation_guard", replacing_guard,
    )
    with pytest.raises(ReadinessError, match="^AWS_DEPLOYMENT_ROOT_CHANGED$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW, systemd_probe=_units(),
            status_probe=_status(), release_probe=release_probe,
        )
    assert observed == []


def test_writable_deployment_root_is_rejected_before_fence_or_probes(tmp_path):
    root = _root(tmp_path)
    root.chmod(0o777)
    observed = []

    def forbidden(*_args, **_kwargs):
        observed.append(True)
        pytest.fail("untrusted root must block every observation")

    with pytest.raises(ReadinessError, match="^AWS_DEPLOYMENT_ROOT_UNTRUSTED$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW, systemd_probe=forbidden,
            status_probe=forbidden, release_probe=forbidden,
        )
    assert observed == []


def test_symlinked_deployment_root_cannot_redirect_readiness(tmp_path):
    actual_root = _root(tmp_path / "actual")
    redirected_root = tmp_path / "hope-operations"
    redirected_root.symlink_to(actual_root, target_is_directory=True)
    observed = []

    def forbidden(*_args, **_kwargs):
        observed.append(True)
        pytest.fail("redirected root must block every observation")

    with pytest.raises(ReadinessError, match="^AWS_DEPLOYMENT_ROOT_UNTRUSTED$"):
        inspect_aws_readiness(
            EXPECTED, root=redirected_root, now=NOW, systemd_probe=forbidden,
            status_probe=forbidden, release_probe=forbidden,
        )
    assert observed == []


def test_deployment_root_replacement_during_resolution_fails_closed(
    tmp_path, monkeypatch,
):
    root = _root(tmp_path / "original")
    replacement = _root(tmp_path / "replacement")
    (replacement / "current").unlink()
    (replacement / "current").symlink_to(Path("releases") / EXPECTED)
    retained = tmp_path / "retained-root"
    original_resolve = Path.resolve
    replaced = False
    observed = []

    def replacing_resolve(path, *args, **kwargs):
        nonlocal replaced
        if path == root and not replaced:
            replaced = True
            root.rename(retained)
            replacement.rename(root)
        return original_resolve(path, *args, **kwargs)

    def forbidden(*_args, **_kwargs):
        observed.append(True)
        pytest.fail("changed root must block every observation")

    monkeypatch.setattr(Path, "resolve", replacing_resolve)
    with pytest.raises(ReadinessError, match="^AWS_DEPLOYMENT_ROOT_CHANGED$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW, systemd_probe=forbidden,
            status_probe=forbidden, release_probe=forbidden,
        )
    assert replaced is True
    assert observed == []


def test_symlinked_release_store_cannot_claim_readiness(tmp_path):
    root = tmp_path / "hope-operations"
    external = tmp_path / "external-releases"
    release = external / EXPECTED
    release.mkdir(parents=True)
    root.mkdir()
    (root / "releases").symlink_to(external, target_is_directory=True)
    (root / "current").symlink_to(release)

    with pytest.raises(ReadinessError, match="^AWS_RELEASE_STORAGE_UNTRUSTED$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW, systemd_probe=_units(),
            status_probe=_status(), release_probe=_release_ok,
        )


@pytest.mark.parametrize("writable", ["releases", "release"])
def test_mutable_release_storage_cannot_claim_readiness(tmp_path, writable):
    root = _root(tmp_path)
    path = root / "releases"
    if writable == "release":
        path /= EXPECTED
    path.chmod(0o777)

    with pytest.raises(ReadinessError, match="^AWS_RELEASE_STORAGE_UNTRUSTED$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW, systemd_probe=_units(),
            status_probe=_status(), release_probe=_release_ok,
        )


def test_missing_release_store_fails_with_sanitized_error(tmp_path):
    root = tmp_path / "hope-operations"
    root.mkdir()

    with pytest.raises(ReadinessError, match="^AWS_RELEASE_STORAGE_UNAVAILABLE$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW, systemd_probe=_units(),
            status_probe=_status(), release_probe=_release_ok,
        )


def test_release_directory_replacement_during_authentication_fails_closed(tmp_path):
    root = _root(tmp_path)
    release = root / "releases" / EXPECTED
    retained = tmp_path / "retained-release"

    def replace_release(_release, _sha):
        release.rename(retained)
        release.mkdir()
        return {"status": "COMPATIBLE_CODE"}

    with pytest.raises(ReadinessError, match="^AWS_RELEASE_STORAGE_CHANGED$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW, systemd_probe=_units(),
            status_probe=_status(), release_probe=replace_release,
        )


def test_release_directory_replacement_during_status_fails_closed(tmp_path):
    root = _root(tmp_path)
    release = root / "releases" / EXPECTED
    retained = tmp_path / "retained-release"

    def replace_release(_url, *, timeout):
        release.rename(retained)
        release.mkdir()
        return _status()(_url, timeout=timeout)

    with pytest.raises(ReadinessError, match="^AWS_RELEASE_STORAGE_CHANGED$"):
        inspect_aws_readiness(
            EXPECTED, root=root, now=NOW, systemd_probe=_units(),
            status_probe=replace_release, release_probe=_release_ok,
        )


def test_healthy_old_dashboard_process_cannot_claim_release_readiness(tmp_path):
    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_units(), status_probe=_status(), release_probe=_release_ok,
        dashboard_release_probe=_dashboard_release("a" * 40),
    )
    assert report["ready"] is False
    assert "DASHBOARD_RELEASE_MISMATCH" in report["blockers"]
    assert report["dashboard_release"] == {
        "schema": "hope.dashboard-release.v1",
        "dashboard_commit": "a" * 40,
        "paper_only": True,
        "live_enabled": False,
        "execution_authorized": False,
    }


def test_dashboard_release_identity_is_exact_and_sanitized(tmp_path):
    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_units(), status_probe=_status(), release_probe=_release_ok,
        dashboard_release_probe=_dashboard_release(secret="must-not-be-reported"),
    )
    assert report["ready"] is False
    assert "DASHBOARD_RELEASE_IDENTITY_INVALID" in report["blockers"]
    assert "secret" not in report["dashboard_release"]


@pytest.mark.parametrize(("field", "unsafe"), (
    ("paper_only", False),
    ("live_enabled", True),
    ("execution_authorized", True),
))
def test_dashboard_release_must_attest_paper_only_safety(tmp_path, field, unsafe):
    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_units(), status_probe=_status(), release_probe=_release_ok,
        dashboard_release_probe=_dashboard_release(**{field: unsafe}),
    )
    assert report["ready"] is False
    assert "DASHBOARD_SAFETY_CONTRACT_MISMATCH" in report["blockers"]


def test_observed_aws_drift_is_reported_without_mutating_release(tmp_path):
    deployed = "f17a54929a8ecb7745a62c118893b55494aeb2f1"
    root = _root(tmp_path, deployed)
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    report = inspect_aws_readiness(
        EXPECTED, root=root, now=NOW,
        systemd_probe=_units(missing="hope-paper.service"),
        release_probe=_release_ok,
        dashboard_release_probe=_dashboard_release(deployed),
        status_probe=lambda _, *, timeout: {
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


def test_status_probe_uses_fixed_bounded_curl_without_ambient_credentials():
    observed = []
    body = (
        '{"schema_version":1,"service":"RUNNING","data_feed":"CURRENT",'
        '"snapshot_generated_at":"2026-10-24T22:55:00+00:00"}'
    )

    def capture(command, **kwargs):
        observed.append((command, kwargs))
        return body + "\n200"

    assert aws_readiness._status_probe(
        "http://127.0.0.1:8788/status.json", capture=capture,
    )["service"] == "RUNNING"
    command, kwargs = observed[0]
    assert command == [
        "/usr/bin/curl", "-q", "--silent", "--show-error",
        "--noproxy", "*", "--proto", "=http", "--max-time", "5",
        "--request", "GET", "--header", "Host: 127.0.0.1",
        "--write-out", "\n%{http_code}",
        "http://127.0.0.1:8788/status.json",
    ]
    assert kwargs == {
        "timeout": 5,
        "max_output": aws_readiness.MAX_STATUS_BYTES + 4,
        "env": {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        },
    }


def test_dashboard_release_probe_is_pinned_and_more_tightly_bounded():
    observed = []
    body = (
        '{"schema":"hope.dashboard-release.v1","dashboard_commit":"'
        + EXPECTED
        + '","paper_only":true,"live_enabled":false,'
        '"execution_authorized":false}'
    )

    def capture(command, **kwargs):
        observed.append((command, kwargs))
        return body + "\n200"

    document = aws_readiness._dashboard_release_probe(
        "http://127.0.0.1:8788/release.json", capture=capture,
    )
    assert document["dashboard_commit"] == EXPECTED
    command, kwargs = observed[0]
    assert command[-1] == "http://127.0.0.1:8788/release.json"
    assert kwargs["max_output"] == aws_readiness.MAX_DASHBOARD_RELEASE_BYTES + 4
    assert "--noproxy" in command
    assert "--proto" in command


@pytest.mark.parametrize(("failure", "error"), (
    (RuntimeError("DIAGNOSTIC_TIMEOUT"), "AWS_STATUS_OBSERVATION_TIMEOUT"),
    (RuntimeError("DIAGNOSTIC_OUTPUT_LIMIT"), "AWS_STATUS_DOCUMENT_TOO_LARGE"),
    (RuntimeError("DIAGNOSTIC_POSIX_REQUIRED"), "AWS_STATUS_OBSERVATION_FAILED"),
    (OSError("private host detail"), "AWS_STATUS_OBSERVATION_FAILED"),
    (
        aws_readiness.subprocess.CalledProcessError(28, ["curl"]),
        "AWS_STATUS_OBSERVATION_TIMEOUT",
    ),
    (
        aws_readiness.subprocess.CalledProcessError(7, ["curl"]),
        "AWS_STATUS_OBSERVATION_FAILED",
    ),
    (
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
        "AWS_STATUS_DOCUMENT_INVALID",
    ),
))
def test_status_capture_failures_are_sanitized(failure, error):
    def fail(*_args, **_kwargs):
        raise failure

    with pytest.raises(ReadinessError, match="^" + error + "$"):
        aws_readiness._status_probe(
            "http://127.0.0.1:8788/status.json", capture=fail,
        )


@pytest.mark.parametrize(("output", "error"), (
    ('{}', "AWS_STATUS_DOCUMENT_INVALID"),
    ('{}\nnot-a-status', "AWS_STATUS_DOCUMENT_INVALID"),
    ('{}\n503', "AWS_STATUS_HTTP_FAILED:503"),
    ('[]\n200', "AWS_STATUS_DOCUMENT_INVALID"),
    ('not-json\n200', "AWS_STATUS_DOCUMENT_INVALID"),
))
def test_status_capture_requires_valid_http_framing_and_document(output, error):
    with pytest.raises(ReadinessError, match="^" + error + "$"):
        aws_readiness._status_probe(
            "http://127.0.0.1:8788/status.json",
            capture=lambda *_args, **_kwargs: output,
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

    def disabled_probe(observed_unit, *, timeout):
        values = base(observed_unit, timeout=timeout)
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

    def exited_probe(observed_unit, *, timeout):
        values = base(observed_unit, timeout=timeout)
        if observed_unit == unit:
            values = {**values, "SubState": "exited"}
        return values

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=exited_probe, status_probe=_status(),
        release_probe=_release_ok,
    )
    assert "UNIT_NOT_RUNNING:" + unit in report["blockers"]


@pytest.mark.parametrize("unit", RUNNING_UNITS)
@pytest.mark.parametrize("change", (
    {"MainPID": "0"},
    {"InvocationID": ""},
    {"InvocationID": "not-an-invocation"},
    {"ExecMainStartTimestampMonotonic": "0"},
))
def test_running_unit_without_concrete_runtime_identity_cannot_claim_readiness(
    tmp_path, unit, change,
):
    base = _units()

    def unidentified_probe(observed_unit, *, timeout):
        values = base(observed_unit, timeout=timeout)
        return {**values, **change} if observed_unit == unit else values

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=unidentified_probe, status_probe=_status(),
        release_probe=_release_ok,
    )
    assert "UNIT_RUNTIME_IDENTITY_INVALID:" + unit in report["blockers"]


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

    def nonrecovering_probe(observed_unit, *, timeout):
        values = base(observed_unit, timeout=timeout)
        if observed_unit == unit:
            values = {**values, property_name: unsafe_value}
        return values

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=nonrecovering_probe, status_probe=_status(),
        release_probe=_release_ok,
    )
    assert "UNIT_RECOVERY_POLICY_MISMATCH:" + unit in report["blockers"]


@pytest.mark.parametrize(("unit", "change"), (
    (
        "hope-paper.service",
        {"FragmentPath": "/run/systemd/system/hope-paper.service"},
    ),
    (
        "hope-paper.service",
        {"DropInPaths": "/run/systemd/system/hope-paper.service.d/override.conf"},
    ),
    (
        "hope-ops-dashboard-web.service",
        {"FragmentPath": "/usr/lib/systemd/system/vendor.service"},
    ),
    (
        "hope-ops-dashboard-export.service",
        {"DropInPaths": "/etc/systemd/system/override.conf"},
    ),
    ("hope-ops-dashboard-export.timer", {"FragmentPath": "/tmp/attacker.timer"}),
    ("hope-dashboard-web.service", {"DropInPaths": "/run/systemd/system/legacy.conf"}),
))
def test_unreviewed_hope_unit_configuration_cannot_claim_readiness(
    tmp_path, unit, change,
):
    base = _units()

    def redirected_probe(observed_unit, *, timeout):
        values = base(observed_unit, timeout=timeout)
        return {**values, **change} if observed_unit == unit else values

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=redirected_probe, status_probe=_status(),
        release_probe=_release_ok,
    )
    assert report["ready"] is False
    assert "UNIT_CONFIGURATION_MISMATCH:" + unit in report["blockers"]


def test_loaded_unit_requiring_daemon_reload_cannot_claim_readiness(tmp_path):
    base = _units()

    def stale_probe(unit, *, timeout):
        values = base(unit, timeout=timeout)
        if unit == "hope-paper.service":
            values = {**values, "NeedDaemonReload": "yes"}
        return values

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=stale_probe, status_probe=_status(),
        release_probe=_release_ok,
    )
    assert report["ready"] is False
    assert "UNIT_CONFIGURATION_MISMATCH:hope-paper.service" in report["blockers"]


def test_installed_unit_bytes_must_match_authenticated_release(tmp_path):
    root = _root(tmp_path)
    observed = []

    def compare(installed, reviewed):
        observed.append((installed, reviewed))
        return installed.name != "hope-paper.service"

    report = inspect_aws_readiness(
        EXPECTED, root=root, now=NOW, systemd_probe=_units(),
        status_probe=_status(), release_probe=_release_ok,
        unit_file_probe=compare,
    )
    assert report["ready"] is False
    assert report["blockers"] == [
        "UNIT_CONFIGURATION_MISMATCH:hope-paper.service",
    ]
    assert observed == [
        (
            Path(HOPE_UNIT_FRAGMENTS[unit]),
            (root / "releases" / EXPECTED / HOPE_UNIT_TEMPLATES[unit]).resolve(),
        )
        for unit in HOPE_UNIT_FRAGMENTS
    ]


def test_default_unit_matcher_uses_system_and_release_trust_anchors(
    tmp_path, monkeypatch,
):
    root = _root(tmp_path)
    observed = []

    def compare(installed, reviewed, **kwargs):
        observed.append((installed, reviewed, kwargs))
        return True

    monkeypatch.setattr(aws_readiness, "_unit_file_matches", compare)
    report = aws_readiness.inspect_aws_readiness(
        EXPECTED, root=root, now=NOW, systemd_probe=_units(),
        status_probe=_status(), dashboard_release_probe=_dashboard_release(),
        release_probe=_release_ok,
    )

    assert report["ready"] is True
    deployed_release = (root / "releases" / EXPECTED).resolve()
    assert observed == [
        (
            Path(HOPE_UNIT_FRAGMENTS[unit]),
            deployed_release / HOPE_UNIT_TEMPLATES[unit],
            {"installed_root": Path("/"), "reviewed_root": deployed_release},
        )
        for unit in HOPE_UNIT_FRAGMENTS
    ]


def test_unit_file_match_rejects_different_bytes(tmp_path):
    installed = tmp_path / "installed.service"
    reviewed = tmp_path / "reviewed.service"
    installed.write_bytes(b"[Service]\nExecStart=/unreviewed\n")
    reviewed.write_bytes(b"[Service]\nExecStart=/reviewed\n")
    assert aws_readiness._unit_file_matches(installed, reviewed) is False


@pytest.mark.parametrize("side", ["installed", "reviewed"])
def test_unit_file_match_rejects_writable_parent_below_trust_anchor(
    tmp_path, side,
):
    installed_root = tmp_path / "installed-root"
    reviewed_root = tmp_path / "reviewed-root"
    installed_parent = installed_root / "etc" / "systemd" / "system"
    reviewed_parent = reviewed_root / "deploy" / "operations"
    installed_parent.mkdir(parents=True)
    reviewed_parent.mkdir(parents=True)
    installed = installed_parent / "hope-paper.service"
    reviewed = reviewed_parent / "hope-paper.service"
    content = b"[Service]\nExecStart=/reviewed\n"
    installed.write_bytes(content)
    reviewed.write_bytes(content)
    (installed_parent if side == "installed" else reviewed_parent).chmod(0o777)

    with pytest.raises(ReadinessError, match="^AWS_UNIT_CONFIGURATION_UNTRUSTED$"):
        aws_readiness._unit_file_matches(
            installed, reviewed, installed_root=installed_root,
            reviewed_root=reviewed_root,
        )


def test_unit_storage_requires_one_owner_from_authenticated_root():
    filesystem_root = (1, 9, stat.S_IFDIR | 0o755, 0, 0)
    directory = (1, 10, stat.S_IFDIR | 0o755, 1000, 1000)
    foreign_directory = (1, 11, stat.S_IFDIR | 0o755, 2000, 2000)
    unit = (1, 12, stat.S_IFREG | 0o644, 1, 10, 1, 1, 1000, 1000)

    with pytest.raises(ReadinessError, match="^AWS_UNIT_CONFIGURATION_UNTRUSTED$"):
        aws_readiness._require_trusted_unit_storage(
            Path("/release/deploy/hope-paper.service"),
            (filesystem_root, directory, foreign_directory, unit),
            trust_root=Path("/release"),
        )


def test_installed_unit_storage_requires_root_owner():
    directory = (1, 10, stat.S_IFDIR | 0o755, 1000, 1000)
    unit = (1, 11, stat.S_IFREG | 0o644, 1, 10, 1, 1, 1000, 1000)

    with pytest.raises(ReadinessError, match="^AWS_UNIT_CONFIGURATION_UNTRUSTED$"):
        aws_readiness._require_trusted_unit_storage(
            Path("/etc/hope-paper.service"), (directory, directory, unit),
            trust_root=Path("/"), required_owner=0,
        )


def test_unit_file_match_detects_installed_replacement_during_comparison(
    tmp_path, monkeypatch,
):
    installed = tmp_path / "installed.service"
    reviewed = tmp_path / "reviewed.service"
    replacement = tmp_path / "attacker.service"
    reviewed_content = b"[Service]\nExecStart=/reviewed\n"
    installed.write_bytes(reviewed_content)
    reviewed.write_bytes(reviewed_content)
    replacement.write_bytes(b"[Service]\nExecStart=/attacker\n")
    real_snapshot = aws_readiness._bounded_unit_file_snapshot

    def replace_while_reading_reviewed(path):
        snapshot = real_snapshot(path)
        if path == reviewed:
            replacement.replace(installed)
        return snapshot

    monkeypatch.setattr(
        aws_readiness, "_bounded_unit_file_snapshot",
        replace_while_reading_reviewed,
    )
    with pytest.raises(ReadinessError, match="^AWS_UNIT_CONFIGURATION_CHANGED$"):
        aws_readiness._unit_file_matches(installed, reviewed)
    assert b"/attacker" in installed.read_bytes()


def test_unit_file_match_detects_parent_replacement_during_comparison(
    tmp_path, monkeypatch,
):
    installed_parent = tmp_path / "installed-systemd"
    replacement_parent = tmp_path / "attacker-systemd"
    retained_parent = tmp_path / "retained-systemd"
    installed_parent.mkdir()
    replacement_parent.mkdir()
    installed = installed_parent / "hope-paper.service"
    reviewed = tmp_path / "reviewed.service"
    reviewed_content = b"[Service]\nExecStart=/reviewed\n"
    installed.write_bytes(reviewed_content)
    reviewed.write_bytes(reviewed_content)
    (replacement_parent / installed.name).write_bytes(
        b"[Service]\nExecStart=/attacker\n",
    )
    real_snapshot = aws_readiness._bounded_unit_file_snapshot

    def replace_parent_while_reading_reviewed(path):
        snapshot = real_snapshot(path)
        if path == reviewed:
            installed_parent.rename(retained_parent)
            replacement_parent.rename(installed_parent)
        return snapshot

    monkeypatch.setattr(
        aws_readiness, "_bounded_unit_file_snapshot",
        replace_parent_while_reading_reviewed,
    )
    with pytest.raises(ReadinessError, match="^AWS_UNIT_CONFIGURATION_CHANGED$"):
        aws_readiness._unit_file_matches(installed, reviewed)
    assert b"/attacker" in installed.read_bytes()


def test_bounded_unit_snapshot_detects_path_replacement_after_read(
    tmp_path, monkeypatch,
):
    path = tmp_path / "unit.service"
    replacement = tmp_path / "attacker.service"
    path.write_bytes(b"[Service]\nExecStart=/reviewed\n")
    replacement.write_bytes(b"[Service]\nExecStart=/attacker\n")
    real_validate = aws_readiness._validate_open_unit_path

    def replace_before_validation(observed_path, descriptors, expected):
        replacement.replace(path)
        return real_validate(observed_path, descriptors, expected)

    monkeypatch.setattr(
        aws_readiness, "_validate_open_unit_path",
        replace_before_validation,
    )
    with pytest.raises(ReadinessError, match="^AWS_UNIT_CONFIGURATION_CHANGED$"):
        aws_readiness._bounded_unit_file_snapshot(path)


@pytest.mark.parametrize(
    "unsafe", ["symlink", "parent_symlink", "writable", "oversized"],
)
def test_bounded_unit_file_rejects_untrusted_storage(tmp_path, unsafe):
    target = tmp_path / "target.service"
    target.write_bytes(b"[Service]\n")
    path = target
    if unsafe == "symlink":
        path = tmp_path / "unit.service"
        path.symlink_to(target)
        expected = "AWS_UNIT_CONFIGURATION_UNAVAILABLE"
    elif unsafe == "parent_symlink":
        actual_parent = tmp_path / "actual-systemd"
        actual_parent.mkdir()
        target.replace(actual_parent / "unit.service")
        redirected_parent = tmp_path / "systemd"
        redirected_parent.symlink_to(actual_parent, target_is_directory=True)
        path = redirected_parent / "unit.service"
        expected = "AWS_UNIT_CONFIGURATION_UNAVAILABLE"
    else:
        if unsafe == "writable":
            target.chmod(0o666)
        else:
            target.write_bytes(b"x" * (aws_readiness.MAX_UNIT_BYTES + 1))
        expected = "AWS_UNIT_CONFIGURATION_UNTRUSTED"
    with pytest.raises(ReadinessError, match="^" + expected + "$"):
        aws_readiness._bounded_unit_file(path)


def test_bounded_unit_file_reader_detects_in_place_change(tmp_path, monkeypatch):
    path = tmp_path / "unit.service"
    path.write_bytes(b"[Service]\n")
    real_read = os.read
    changed = [False]

    def mutate_after_read(descriptor, size):
        content = real_read(descriptor, size)
        if content and not changed[0]:
            changed[0] = True
            with path.open("ab") as stream:
                stream.write(b"changed\n")
        return content

    monkeypatch.setattr(os, "read", mutate_after_read)
    with pytest.raises(ReadinessError, match="^AWS_UNIT_CONFIGURATION_CHANGED$"):
        aws_readiness._bounded_unit_file(path)


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
        dashboard_release_probe=_dashboard_release(deployed),
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
        "FragmentPath=/etc/systemd/system/hope-paper.service", "DropInPaths=",
        "NeedDaemonReload=no",
        "MainPID=4321", f"InvocationID={INVOCATION_ID}",
        "ExecMainStartTimestampMonotonic=123456789",
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


def test_systemd_observations_share_one_total_deadline(tmp_path):
    clock = [100.0]
    observed = []
    status_called = []

    def monotonic():
        return clock[0]

    def slow_probe(unit, *, timeout):
        observed.append((unit, timeout))
        clock[0] += 2
        return _units()(unit, timeout=timeout)

    with pytest.raises(
        ReadinessError,
        match="^AWS_SYSTEMD_OBSERVATION_TIMEOUT:hope-ops-dashboard-export.service$",
    ):
        inspect_aws_readiness(
            EXPECTED, root=_root(tmp_path), now=NOW,
            systemd_probe=slow_probe,
            status_probe=lambda _url: status_called.append(True),
            release_probe=_release_ok,
            monotonic=monotonic,
        )

    assert observed == [
        ("postgresql.service", 5),
        ("hope-dashboard-web.service", 3),
        ("hope-ops-dashboard-web.service", 1),
    ]
    assert status_called == []


@pytest.mark.parametrize(
    ("changed_key", "changed_value"),
    [
        ("ActiveState", "inactive"),
        ("SubState", "stop-sigterm"),
        ("MainPID", "9876"),
        ("InvocationID", "2" * 32),
        ("ExecMainStartTimestampMonotonic", "987654321"),
    ],
)
@pytest.mark.parametrize("unit", RUNNING_UNITS)
def test_restart_during_loopback_observation_cannot_claim_readiness(
    tmp_path, unit, changed_key, changed_value,
):
    base = _units()
    observations = {name: 0 for name in RUNNING_UNITS}

    def restarting_probe(observed_unit, *, timeout):
        values = base(observed_unit, timeout=timeout)
        if observed_unit in observations:
            observations[observed_unit] += 1
            if observations[observed_unit] == 2 and observed_unit == unit:
                values = {**values, changed_key: changed_value}
        return values

    with pytest.raises(
        ReadinessError,
        match="^AWS_SYSTEMD_RUNTIME_CHANGED:" + re.escape(unit) + "$",
    ):
        inspect_aws_readiness(
            EXPECTED, root=_root(tmp_path), now=NOW,
            systemd_probe=restarting_probe, status_probe=_status(),
            release_probe=_release_ok,
        )


def test_runtime_revalidation_has_one_total_deadline(tmp_path):
    clock = [100.0]
    base = _units()
    observations = {unit: 0 for unit in REQUIRED_UNITS}

    def monotonic():
        return clock[0]

    def slow_second_probe(unit, *, timeout):
        observations[unit] += 1
        if unit in RUNNING_UNITS and observations[unit] == 2:
            clock[0] += 2
        return base(unit, timeout=timeout)

    with pytest.raises(
        ReadinessError,
        match=(
            "^AWS_SYSTEMD_REVALIDATION_TIMEOUT:"
            "hope-paper\\.service$"
        ),
    ):
        inspect_aws_readiness(
            EXPECTED, root=_root(tmp_path), now=NOW,
            systemd_probe=slow_second_probe, status_probe=_status(),
            release_probe=_release_ok, monotonic=monotonic,
        )


def test_dashboard_identity_and_status_share_one_total_deadline(tmp_path):
    clock = [100.0]
    observed = []

    def monotonic():
        return clock[0]

    def release_probe(_url, *, timeout):
        observed.append(("dashboard-release", timeout))
        clock[0] += 4
        return _dashboard_release()(_url, timeout=timeout)

    def status_probe(_url, *, timeout):
        observed.append(("status", timeout))
        return _status()(_url, timeout=timeout)

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_units(), status_probe=status_probe,
        dashboard_release_probe=release_probe, release_probe=_release_ok,
        monotonic=monotonic,
    )
    assert report["ready"] is True
    assert observed == [("dashboard-release", 5), ("status", 1)]


def test_exhausted_dashboard_identity_deadline_prevents_status_probe(tmp_path):
    clock = [100.0]
    status_called = []

    def monotonic():
        return clock[0]

    def release_probe(_url, *, timeout):
        clock[0] += timeout
        return _dashboard_release()(_url, timeout=timeout)

    with pytest.raises(ReadinessError, match="^AWS_STATUS_OBSERVATION_TIMEOUT$"):
        inspect_aws_readiness(
            EXPECTED, root=_root(tmp_path), now=NOW,
            systemd_probe=_units(),
            status_probe=lambda *_args, **_kwargs: status_called.append(True),
            dashboard_release_probe=release_probe, release_probe=_release_ok,
            monotonic=monotonic,
        )
    assert status_called == []


def test_boolean_status_schema_cannot_impersonate_version_one(tmp_path):
    def boolean_schema(_url, *, timeout):
        return {
            **_status()(_url, timeout=timeout),
            "schema_version": True,
        }

    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_units(), status_probe=boolean_schema,
        release_probe=_release_ok,
    )
    assert report["ready"] is False
    assert "OPERATIONS_STATUS_NOT_RUNNING" in report["blockers"]


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
        "FragmentPath=/etc/systemd/system/hope-paper.service", "DropInPaths=",
        "NeedDaemonReload=no",
        "MainPID=4321", f"InvocationID={INVOCATION_ID}",
        "ExecMainStartTimestampMonotonic=123456789",
        *(key + "=" + value for key, value in policy.items()),
    )) + "\n"

    def run(command, **kwargs):
        observed.append((command, kwargs))
        return stdout

    assert aws_readiness._systemd_probe("hope-paper.service", capture=run) == {
        "LoadState": "loaded", "ActiveState": "active", "SubState": "running",
        "UnitFileState": "enabled", "NRestarts": "0",
        "FragmentPath": "/etc/systemd/system/hope-paper.service",
        "DropInPaths": "", "NeedDaemonReload": "no", **policy,
        "MainPID": "4321", "InvocationID": INVOCATION_ID,
        "ExecMainStartTimestampMonotonic": "123456789",
    }
    command, kwargs = observed[0]
    assert command[:4] == [
        "/usr/bin/systemctl", "show", "hope-paper.service", "--no-pager",
    ]
    assert command.count("-p") == 15
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

    def systemd_probe(unit, *, timeout):
        assert_mutation_excluded("systemd:" + unit)
        return _units()(unit, timeout=timeout)

    def dashboard_release_probe(_url, *, timeout):
        assert_mutation_excluded("dashboard-release")
        return _dashboard_release()(_url, timeout=timeout)

    def status_probe(_url, *, timeout):
        assert_mutation_excluded("status")
        return _status()(_url, timeout=timeout)

    report = inspect_aws_readiness(
        EXPECTED, root=root, now=NOW, systemd_probe=systemd_probe,
        status_probe=status_probe, release_probe=release_probe,
        dashboard_release_probe=dashboard_release_probe,
    )
    assert report["ready"] is True
    assert observations == [
        "release", *("systemd:" + unit for unit in REQUIRED_UNITS),
        "dashboard-release", "status",
        *("systemd:" + unit for unit in RUNNING_UNITS),
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
