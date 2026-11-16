"""Native publication crash tests: never authorize from a torn snapshot pair."""
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal

import pytest

from hope.api import dashboard_snapshot_refresh as refresh
from hope.api.dashboard_server import _verified_refresh_status

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Actual POSIX flock/crash boundary")
COMMIT = "a" * 40


def snapshot(now, count=0):
    return json.dumps({
        "schema_version": 2, "source": "hope_postgresql", "generated_at": now.isoformat(),
        "deployed_commit": COMMIT, "research": {"run_count": count},
        "paper": {"environment_state": "RUNNING", "job_runs": {
            "total": 0, "claimed": 0, "succeeded": 0, "failed": 0}},
        "provenance": {"data_classification": "DISPOSABLE_LAB",
            "database_identity_sha256": "b" * 64, "synthetic_activity": True,
            "execution_authorized": False, "live_enabled": False},
    }).encode()


@pytest.mark.parametrize("alias", ["snapshot", "lock", "hardlink", "symlink"])
def test_conflicting_publication_paths_refuse_before_export(tmp_path, monkeypatch, alias):
    output = tmp_path / "snapshot.json"
    output.write_bytes(b"last-good")
    status = tmp_path / "status.json"
    if alias == "snapshot":
        status = output
    elif alias == "lock":
        status = refresh._refresh_lock_path(output)
    elif alias == "hardlink":
        os.link(output, status)
    else:
        status.symlink_to(output)
    called = []
    now = datetime.now(timezone.utc)
    def export(path, *args, **kwargs):
        called.append(True)
        path.write_bytes(snapshot(now))
    monkeypatch.setattr(refresh, "export_dashboard_snapshot", export)
    with pytest.raises(ValueError, match="HOPE_DASHBOARD_REFRESH_PATHS_INVALID"):
        refresh.refresh_once(output, status, "unused", expected_database="lab",
                             deployed_commit=COMMIT, observed_at=now)
    assert called == []
    assert output.read_bytes() == b"last-good"


@pytest.mark.parametrize("kind", ["oversize", "symlink", "hardlink"])
def test_untrusted_staged_file_preserves_last_good(tmp_path, monkeypatch, kind):
    output, status = tmp_path / "snapshot.json", tmp_path / "status.json"
    output.write_bytes(b"last-good")
    now = datetime.now(timezone.utc)
    victim = tmp_path / "private-source"
    victim.write_bytes(snapshot(now))
    def export(path, *args, **kwargs):
        if kind == "oversize":
            path.write_bytes(b" " * 32769)
        elif kind == "symlink":
            path.symlink_to(victim)
        else:
            os.link(victim, path)
    monkeypatch.setattr(refresh, "export_dashboard_snapshot", export)
    assert not refresh.refresh_once(output, status, "unused", expected_database="lab",
                                  deployed_commit=COMMIT, observed_at=now)
    assert output.read_bytes() == b"last-good"
    assert victim.read_bytes() == snapshot(now)
    result = json.loads(status.read_bytes())
    assert result["state"] == "FAILED"
    assert result["error_code"] == "SNAPSHOT_REFRESH_FAILED"
    assert result["snapshot_sha256"] is None


@pytest.mark.parametrize("stage", ["export", "snapshot", "receipt"])
def test_actual_publisher_death_preserves_coherence_and_restart(tmp_path, monkeypatch, stage):
    context = multiprocessing.get_context("fork")
    output, status = tmp_path / "snapshot.json", tmp_path / "status.json"
    now = datetime.now(timezone.utc)
    old, new = snapshot(now, 1), snapshot(now, 2)
    monkeypatch.setattr(refresh, "export_dashboard_snapshot",
                        lambda path, *args, **kwargs: path.write_bytes(old))
    assert refresh.refresh_once(output, status, "unused", expected_database="lab",
                                deployed_commit=COMMIT, observed_at=now)
    old_status = status.read_bytes()
    receive, send = context.Pipe(duplex=False)
    release = context.Event()
    def publish():
        receive.close()
        def pause():
            send.send("paused")
            if not release.wait(20):
                raise RuntimeError("test parent failed to terminate publisher")
        def export(path, *args, **kwargs):
            path.write_bytes(new)
            if stage == "export":
                pause()
        real_replace = refresh.durable_replace
        def replace(source, target):
            real_replace(source, target)
            if (stage == "snapshot" and Path(target) == output
                    or stage == "receipt" and Path(target) == status):
                pause()
        refresh.export_dashboard_snapshot = export
        refresh.durable_replace = replace
        refresh.refresh_once(output, status, "unused", expected_database="lab",
                             deployed_commit=COMMIT, observed_at=now)
    process = context.Process(target=publish)
    process.start()
    send.close()
    try:
        assert receive.poll(15), "publisher did not reach injected crash boundary"
        assert receive.recv() == "paused"
        with pytest.raises(refresh._RefreshLockUnavailable):
            refresh._acquire_refresh_lock(output)
        os.kill(process.pid, signal.SIGKILL)
        process.join(5)
        assert process.exitcode == -signal.SIGKILL
        assert output.read_bytes() == (old if stage == "export" else new)
        if stage != "receipt":
            assert status.read_bytes() == old_status
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        if stage == "snapshot":
            with pytest.raises(ValueError, match="REFRESH_SNAPSHOT_MISMATCH"):
                _verified_refresh_status(status, expected_commit=COMMIT,
                    expected_snapshot_generated=now, expected_snapshot_sha256=digest,
                    expected_snapshot_data_classification='DISPOSABLE_LAB',
                    expected_snapshot_database_identity_sha256='b'*64,
                    expected_snapshot_synthetic_activity=True,
                    require_snapshot_match=True)
        else:
            assert json.loads(_verified_refresh_status(status, expected_commit=COMMIT,
                expected_snapshot_generated=now, expected_snapshot_sha256=digest,
                    expected_snapshot_data_classification='DISPOSABLE_LAB',
                    expected_snapshot_database_identity_sha256='b'*64,
                    expected_snapshot_synthetic_activity=True,
                require_snapshot_match=True))["state"] == "CURRENT"
        # A new process can acquire the persistent inode after actual death.
        monkeypatch.setattr(refresh, "export_dashboard_snapshot",
                            lambda path, *args, **kwargs: path.write_bytes(new))
        assert refresh.refresh_once(output, status, "unused", expected_database="lab",
                                    deployed_commit=COMMIT, observed_at=now)
        result = json.loads(status.read_bytes())
        assert result["snapshot_sha256"] == hashlib.sha256(new).hexdigest()
        assert result["execution_authorized"] is False
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        receive.close()
