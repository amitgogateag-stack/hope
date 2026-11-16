"""The LAB refresher preserves last-good bytes and never claims authority."""
from datetime import datetime, timezone
import json
import os
import sys

import pytest

from hope.api import dashboard_snapshot_refresh as module

NOW = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40


def snapshot() -> bytes:
    return json.dumps({
        "schema_version": 2, "source": "hope_postgresql",
        "generated_at": NOW.isoformat(), "deployed_commit": COMMIT,
        "research": {"run_count": 0},
        "paper": {"environment_state": "RUNNING", "job_runs": {
            "total": 0, "claimed": 0, "succeeded": 0, "failed": 0,
        }},
        "provenance": {"data_classification": "DISPOSABLE_LAB",
            "database_identity_sha256": "b"*64, "synthetic_activity": True,
            "execution_authorized": False, "live_enabled": False},
    }).encode()


def test_success_requires_exact_lab_provenance_and_publishes_non_authorizing_status(tmp_path, monkeypatch):
    output, status = tmp_path/"snapshot.json", tmp_path/"refresh.json"
    called = {}
    def export(path, url, **kwargs):
        called.update(url=url, **kwargs); path.write_bytes(snapshot())
    monkeypatch.setattr(module, "export_dashboard_snapshot", export)
    assert module.refresh_once(output, status, "private-url", expected_database="lab_db",
                               deployed_commit=COMMIT, observed_at=NOW)
    assert called == {"url":"private-url", "deployed_commit":COMMIT,
                      "data_classification":"DISPOSABLE_LAB", "expected_database":"lab_db"}
    result=json.loads(status.read_text())
    assert result["state"]=="CURRENT" and result["error_code"] is None
    assert result["snapshot_generated_at"]==NOW.isoformat()
    assert result["scope"]=="DISPOSABLE_LAB_ONLY"
    assert result["execution_authorized"] is False and result["live_enabled"] is False
    assert "private-url" not in status.read_text() and "lab_db" not in status.read_text()


def test_failure_preserves_last_good_and_emits_only_closed_error(tmp_path, monkeypatch):
    output, status = tmp_path/"snapshot.json", tmp_path/"refresh.json"
    output.write_bytes(b"previous verified snapshot")
    def fail(*args, **kwargs):
        raise RuntimeError("password=secret host=private HOPE_DASHBOARD_DATABASE_IDENTITY_MISMATCH")
    monkeypatch.setattr(module, "export_dashboard_snapshot", fail)
    assert not module.refresh_once(output, status, "secret-url", expected_database="lab_db",
                                   deployed_commit=COMMIT, observed_at=NOW)
    assert output.read_bytes()==b"previous verified snapshot"
    result=json.loads(status.read_text())
    assert result["state"]=="FAILED" and result["error_code"]=="SNAPSHOT_REFRESH_FAILED"
    assert result["snapshot_generated_at"] is None
    assert "secret" not in status.read_text() and "private" not in status.read_text()


def test_exact_identity_failure_has_specific_non_sensitive_code(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("HOPE_DASHBOARD_DATABASE_IDENTITY_MISMATCH")
    monkeypatch.setattr(module, "export_dashboard_snapshot", fail)
    assert not module.refresh_once(tmp_path/"snapshot", tmp_path/"status", "url",
                                   expected_database="lab_db", deployed_commit=COMMIT, observed_at=NOW)
    assert json.loads((tmp_path/"status").read_text())["error_code"]=="DATABASE_IDENTITY_MISMATCH"


def test_post_export_contract_failure_cannot_replace_status_with_current(tmp_path, monkeypatch):
    output, status = tmp_path/"snapshot", tmp_path/"status"
    output.write_bytes(b"previous verified snapshot")
    monkeypatch.setattr(module, "export_dashboard_snapshot",
                        lambda path, *args, **kwargs: path.write_bytes(b"not-json"))
    assert not module.refresh_once(output, status, "url", expected_database="lab_db",
                                   deployed_commit=COMMIT, observed_at=NOW)
    assert output.read_bytes()==b"previous verified snapshot"
    assert json.loads(status.read_text())["state"]=="FAILED"


@pytest.mark.skipif(os.name != "posix", reason="Linux flock publisher boundary")
def test_concurrent_refresher_cannot_export_or_overwrite_active_receipt(tmp_path, monkeypatch):
    output, status = tmp_path/"snapshot", tmp_path/"status"
    status.write_text('{"state":"ACTIVE_PUBLISHER"}\n')
    held = module._acquire_refresh_lock(output)
    called = False
    def export(*args, **kwargs):
        nonlocal called
        called = True
    monkeypatch.setattr(module, "export_dashboard_snapshot", export)
    try:
        assert not module.refresh_once(
            output, status, "url", expected_database="lab_db",
            deployed_commit=COMMIT, observed_at=NOW,
        )
    finally:
        module._release_refresh_lock(held)
    assert called is False
    assert status.read_text() == '{"state":"ACTIVE_PUBLISHER"}\n'
    assert not output.exists()


@pytest.mark.skipif(os.name != "posix", reason="Linux no-follow publisher boundary")
def test_symlink_refresh_lock_fails_closed_without_touching_snapshot_or_receipt(tmp_path, monkeypatch):
    output, status = tmp_path/"snapshot", tmp_path/"status"
    output.write_bytes(b"last-good")
    status.write_bytes(b"last-receipt")
    victim = tmp_path/"victim"
    victim.write_bytes(b"do-not-open")
    module._refresh_lock_path(output).symlink_to(victim)
    monkeypatch.setattr(
        module, "export_dashboard_snapshot",
        lambda *args, **kwargs: pytest.fail("unsafe lock must stop before export"),
    )
    assert not module.refresh_once(
        output, status, "url", expected_database="lab_db",
        deployed_commit=COMMIT, observed_at=NOW,
    )
    assert output.read_bytes() == b"last-good"
    assert status.read_bytes() == b"last-receipt"
    assert victim.read_bytes() == b"do-not-open"


@pytest.mark.skipif(os.name != "posix", reason="Linux private lock metadata")
def test_refresh_lock_is_persistent_private_regular_file(tmp_path, monkeypatch):
    output, status = tmp_path/"snapshot", tmp_path/"status"
    monkeypatch.setattr(
        module, "export_dashboard_snapshot",
        lambda path, *args, **kwargs: path.write_bytes(snapshot()),
    )
    assert module.refresh_once(
        output, status, "url", expected_database="lab_db",
        deployed_commit=COMMIT, observed_at=NOW,
    )
    lock = module._refresh_lock_path(output)
    assert lock.is_file() and not lock.is_symlink()
    assert lock.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="Linux flock publisher boundary")
def test_staging_failure_releases_lock_for_next_refresh(tmp_path, monkeypatch):
    output, status = tmp_path/"snapshot", tmp_path/"status"
    real_mkstemp = module.tempfile.mkstemp
    monkeypatch.setattr(
        module.tempfile, "mkstemp",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    with pytest.raises(OSError, match="disk unavailable"):
        module.refresh_once(
            output, status, "url", expected_database="lab_db",
            deployed_commit=COMMIT, observed_at=NOW,
        )
    monkeypatch.setattr(module.tempfile, "mkstemp", real_mkstemp)
    held = module._acquire_refresh_lock(output)
    module._release_refresh_lock(held)


def test_one_shot_cli_returns_failure_when_no_verified_snapshot_was_published(tmp_path, monkeypatch):
    output, status = tmp_path/"snapshot", tmp_path/"status"
    monkeypatch.setenv("HOPE_DATABASE_URL", "secret-url")
    monkeypatch.setattr(module, "refresh_once", lambda *args, **kwargs: False)
    monkeypatch.setattr(sys, "argv", [
        "hope-dashboard-snapshot-refresh", "--output", str(output),
        "--status", str(status), "--expected-database", "lab_db",
        "--deployed-commit", COMMIT,
    ])
    assert module.main() == 1


def test_one_shot_cli_returns_success_only_after_verified_publication(tmp_path, monkeypatch):
    output, status = tmp_path/"snapshot", tmp_path/"status"
    monkeypatch.setenv("HOPE_DATABASE_URL", "secret-url")
    observed = {}
    def refresh(*args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return True
    monkeypatch.setattr(module, "refresh_once", refresh)
    monkeypatch.setattr(sys, "argv", [
        "hope-dashboard-snapshot-refresh", "--output", str(output),
        "--status", str(status), "--expected-database", "lab_db",
        "--deployed-commit", COMMIT,
    ])
    assert module.main() == 0
    assert observed == {"args": (output, status, "secret-url"), "kwargs": {
        "expected_database": "lab_db", "deployed_commit": COMMIT,
    }}
