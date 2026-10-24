from datetime import datetime, timezone
from pathlib import Path

import pytest

from hope.api.aws_readiness import REQUIRED_UNITS, ReadinessError, inspect_aws_readiness


EXPECTED = "b" * 40
NOW = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)


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
            return {
                "LoadState": "not-found", "ActiveState": "inactive",
                "SubState": "dead", "UnitFileState": "", "NRestarts": "0",
            }
        return {
            "LoadState": "loaded", "ActiveState": "active",
            "SubState": "running", "UnitFileState": "enabled", "NRestarts": "0",
        }
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
        systemd_probe=_units(), status_probe=_status(),
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
            systemd_probe=_units(), status_probe=_status(),
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
    )
    assert blocker in report["blockers"]


def test_status_probe_is_pinned_to_private_operations_endpoint(tmp_path):
    with pytest.raises(ReadinessError, match="^AWS_STATUS_URL_NOT_APPROVED$"):
        inspect_aws_readiness(
            EXPECTED, root=_root(tmp_path), now=NOW,
            systemd_probe=_units(), status_probe=_status(),
            status_url="http://example.invalid/status.json",
        )


@pytest.mark.parametrize(
    "unit", ["hope-ops-dashboard-export.timer", "hope-paper.service"],
)
def test_autonomous_units_must_be_active_for_operational_readiness(tmp_path, unit):
    report = inspect_aws_readiness(
        EXPECTED, root=_root(tmp_path), now=NOW,
        systemd_probe=_inactive(unit), status_probe=_status(),
    )
    assert "UNIT_NOT_ACTIVE:" + unit in report["blockers"]
