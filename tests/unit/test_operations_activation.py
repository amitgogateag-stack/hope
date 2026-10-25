import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture
def activation(tmp_path, monkeypatch):
    path = Path(__file__).parents[2] / "deploy/operations/activate_release.py"
    spec = importlib.util.spec_from_file_location("m1_activation_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.HEALTH = tmp_path / "health.json"
    module.SNAPSHOT = tmp_path / "snapshot.json"
    monkeypatch.setattr(module.os, "geteuid", lambda: 0, raising=False)
    return module


@pytest.mark.parametrize("failure", ["preflight", "start", "timeout", "dashboard", None])
def test_activation_preflight_and_failed_acceptance_leave_services_safe(activation, monkeypatch, failure):
    module = activation
    old, target = "1" * 40, "2" * 40
    monkeypatch.setattr(sys, "argv", ["activate", "--target", target, "--expected-current", old, "--approve-host-change"])
    commands, switches, metadata = [], [], []
    def inspect(*args):
        if failure == "preflight":
            raise RuntimeError("preflight denied")
    monkeypatch.setattr(module, "inspect_release", inspect)
    def systemctl(*args):
        commands.append(args)
        if failure == "start" and args[0] == "start":
            raise RuntimeError("start failed")
        if failure is None and args[0] == "start":
            module.HEALTH.write_text(json.dumps(valid_health(target)))
    monkeypatch.setattr(module, "systemctl", systemctl)
    monkeypatch.setattr(module, "accepted_dashboard", lambda started: failure is None)
    monkeypatch.setattr(module, "metadata", metadata.append)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    module.HEALTH.write_text(json.dumps({"deployed_commit": target, "state": "CURRENT" if failure is None else "UNAVAILABLE"}))
    def switch(root, sha, *, expected_current, accept, before_switch):
        assert expected_current == old
        before_switch()
        switches.append(sha)
        try:
            accept()
        except BaseException:
            switches.append(old)
            raise
    monkeypatch.setattr(module, "switch_release", switch)
    if failure:
        with pytest.raises(RuntimeError):
            module.main()
        if failure == "preflight":
            assert not commands and not switches and not metadata
        else:
            assert switches == [target, old]
            assert commands[-1][0] == "stop"
            assert metadata == [target, old]
    else:
        module.main()
        assert switches == [target] and metadata == [target]
        assert commands[0][0] == "stop" and commands[1][0] == "start"
        assert any(command == ("is-active", "--quiet", *module.UNITS) for command in commands)


def test_metadata_is_atomically_published_with_no_secret_or_extra_fields(activation):
    activation.metadata("a" * 40)
    assert (activation.ROOT / "deployed.env").read_text() == "HOPE_DEPLOYED_COMMIT=" + "a" * 40 + "\n"
    assert stat.S_IMODE((activation.ROOT / "deployed.env").stat().st_mode) == 0o600
    assert sorted(p.name for p in activation.ROOT.iterdir()) == ["deployed.env"]


def test_metadata_fsyncs_published_file_before_parent_directory(activation, monkeypatch):
    calls = []
    real_fsync = activation.os.fsync

    def fsync(fd):
        calls.append("directory" if os.path.isdir(f"/proc/self/fd/{fd}") else "file")
        real_fsync(fd)

    monkeypatch.setattr(activation.os, "fsync", fsync)
    activation.metadata("b" * 40)
    assert calls == ["file", "directory"]


def test_metadata_authenticates_kernel_created_owner_not_preflight_stub(activation, monkeypatch):
    monkeypatch.setattr(activation.os, "geteuid", lambda: 12345)
    activation.metadata("c" * 40)
    assert (activation.ROOT / "deployed.env").read_text().endswith("c" * 40 + "\n")


def test_metadata_staged_fsync_failure_preserves_previous_value(activation, monkeypatch):
    previous = activation.ROOT / "deployed.env"
    previous.write_text("HOPE_DEPLOYED_COMMIT=" + "1" * 40 + "\n")
    previous.chmod(0o600)

    def fail(_fd):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(activation.os, "fsync", fail)
    with pytest.raises(RuntimeError, match="OPERATIONS_METADATA_DURABILITY_FAILED"):
        activation.metadata("2" * 40)
    assert previous.read_text() == "HOPE_DEPLOYED_COMMIT=" + "1" * 40 + "\n"
    assert sorted(path.name for path in activation.ROOT.iterdir()) == ["deployed.env"]


def test_metadata_staged_permission_failure_cleans_exact_temp(activation, monkeypatch):
    monkeypatch.setattr(
        activation.os,
        "fchmod",
        lambda _fd, _mode: (_ for _ in ()).throw(OSError("simulated permission failure")),
    )
    with pytest.raises(RuntimeError, match="OPERATIONS_METADATA_DURABILITY_FAILED"):
        activation.metadata("3" * 40)
    assert list(activation.ROOT.iterdir()) == []


def test_metadata_root_replacement_before_publish_fails_without_touching_replacement(
    activation, monkeypatch, tmp_path,
):
    root = tmp_path / "root"
    detached = tmp_path / "detached"
    root.mkdir()
    activation.ROOT = root
    real_token = activation.secrets.token_hex
    replaced = False

    def replace_root(length):
        nonlocal replaced
        if not replaced:
            root.rename(detached)
            root.mkdir()
            replaced = True
        return real_token(length)

    monkeypatch.setattr(activation.secrets, "token_hex", replace_root)
    with pytest.raises(RuntimeError, match="OPERATIONS_METADATA_ROOT_CHANGED"):
        activation.metadata("4" * 40)
    assert list(root.iterdir()) == []
    assert list(detached.iterdir()) == []


def test_metadata_root_replacement_during_directory_sync_cannot_report_success(
    activation, monkeypatch, tmp_path,
):
    root = tmp_path / "root"
    detached = tmp_path / "detached"
    root.mkdir()
    activation.ROOT = root
    real_fsync = activation.os.fsync
    replaced = False

    def replace_during_sync(fd):
        nonlocal replaced
        if not replaced and os.path.isdir(f"/proc/self/fd/{fd}"):
            root.rename(detached)
            root.mkdir()
            replaced = True
        real_fsync(fd)

    monkeypatch.setattr(activation.os, "fsync", replace_during_sync)
    with pytest.raises(RuntimeError, match="OPERATIONS_METADATA_ROOT_CHANGED"):
        activation.metadata("5" * 40)
    assert list(root.iterdir()) == []
    assert (detached / "deployed.env").read_text().endswith("5" * 40 + "\n")


def test_metadata_rejects_writable_deployment_root_before_staging(activation):
    activation.ROOT.chmod(0o777)
    with pytest.raises(RuntimeError, match="OPERATIONS_METADATA_ROOT_CHANGED"):
        activation.metadata("6" * 40)
    assert list(activation.ROOT.iterdir()) == []


@pytest.mark.parametrize("value", ["a" * 39, "A" * 40, "../" + "a" * 40, None])
def test_metadata_rejects_invalid_commit_before_creating_files(activation, value):
    with pytest.raises(ValueError, match="OPERATIONS_METADATA_COMMIT_INVALID"):
        activation.metadata(value)
    assert list(activation.ROOT.iterdir()) == []


def test_service_control_ignores_ambient_path_and_loader_environment(activation, monkeypatch):
    calls = []
    monkeypatch.setattr(activation.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setenv("PATH", "/hostile/bin")
    monkeypatch.setenv("LD_PRELOAD", "/hostile/inject.so")
    activation.systemctl("show", "hope-paper.service")
    assert calls[0][0][0] == ["/usr/bin/systemctl", "show", "hope-paper.service"]
    assert calls[0][1]["env"] == activation.COMMAND_ENVIRONMENT
    assert "LD_PRELOAD" not in calls[0][1]["env"]


def valid_health(target):
    timestamp = datetime.now(UTC).isoformat()
    return dict(deployed_commit=target, generated_at=timestamp, last_success_at=timestamp,
                state="CURRENT", schema_compatibility="COMPATIBLE", paper_only=True, live_enabled=False)


def valid_snapshot(timestamp=None):
    timestamp = timestamp or datetime.now(UTC)
    return {
        "schema_version": 1, "source": "hope_postgresql",
        "generated_at": timestamp.isoformat(), "research": {"run_count": 0},
        "paper": {
            "environment_state": "RUNNING",
            "job_runs": {"total": 0, "claimed": 0, "succeeded": 0, "failed": 0},
        },
    }


@pytest.mark.parametrize("change", [dict(state="STARTING"), dict(schema_compatibility="UNKNOWN"),
    dict(paper_only=False), dict(live_enabled=True), dict(last_success_at=None),
    dict(generated_at="bad"), dict(deployed_commit="2"*40)])
def test_activation_rejects_old_or_unverified_heartbeat(activation, change):
    started = datetime.now(UTC)
    target = "1" * 40
    value = valid_health(target)
    assert activation.accepted_health(value, target, started)
    assert not activation.accepted_health({**value, **change}, target, started)
    assert not activation.accepted_health(value, target, started + timedelta(seconds=1))


def test_dashboard_acceptance_requires_fresh_export_and_available_loopback_feed(activation, monkeypatch):
    started = datetime.now(UTC)
    activation.SNAPSHOT.write_text(json.dumps(valid_snapshot(started)))
    # Filesystem mtime may lag wall-clock reads; explicitly model a new export.
    os.utime(activation.SNAPSHOT, (started.timestamp(), started.timestamp()))
    monkeypatch.setattr(activation, "verify_dashboard", lambda base, **kwargs: {
        "service": "RUNNING", "paper_feed": "AVAILABLE", "legacy_feed": "UNAVAILABLE",
    })
    assert activation.accepted_dashboard(started)

    monkeypatch.setattr(activation, "verify_dashboard", lambda base, **kwargs: {
        "service": "RUNNING", "paper_feed": "UNAVAILABLE", "legacy_feed": "UNAVAILABLE",
    })
    assert not activation.accepted_dashboard(started)

    monkeypatch.setattr(activation, "verify_dashboard", lambda base, **kwargs: {
        "service": "RUNNING", "paper_feed": "AVAILABLE", "legacy_feed": "UNAVAILABLE",
    })
    old = started - timedelta(minutes=10)
    activation.SNAPSHOT.write_text(json.dumps(valid_snapshot(old)))
    os.utime(activation.SNAPSHOT, (old.timestamp(), old.timestamp()))
    assert not activation.accepted_dashboard(started)


@pytest.mark.parametrize("payload", ["partial{", "[]", '{"schema_version":true}'])
def test_dashboard_acceptance_rejects_malformed_export_without_probing(activation, monkeypatch, payload):
    activation.SNAPSHOT.write_text(payload)
    monkeypatch.setattr(
        activation, "verify_dashboard", lambda *_: pytest.fail("invalid export must fail before HTTP probe")
    )
    assert not activation.accepted_dashboard(datetime.now(UTC))


@pytest.mark.parametrize("seconds", [-240, -1, 1, 240])
def test_rewritten_old_or_future_snapshot_cannot_pass_activation(activation, monkeypatch, seconds):
    started = datetime.now(UTC)
    activation.SNAPSHOT.write_text(json.dumps(valid_snapshot(started+timedelta(seconds=seconds))))
    monkeypatch.setattr(activation,"verify_dashboard",lambda *a,**k: pytest.fail("non-new snapshot must fail before HTTP"))
    assert not activation.accepted_dashboard(started)


def test_activation_passes_exact_verified_export_to_strict_probe(activation,monkeypatch):
    started = datetime.now(UTC)
    activation.SNAPSHOT.write_text(json.dumps(valid_snapshot(started)))
    # Filesystem mtime may lag wall-clock reads; explicitly model a new export.
    os.utime(activation.SNAPSHOT, (started.timestamp(), started.timestamp()))
    captured={}
    def verify(base,**kwargs):
        captured.update(kwargs)
        return dict(service="RUNNING",paper_feed="AVAILABLE")
    monkeypatch.setattr(activation,"verify_dashboard",verify)
    assert activation.accepted_dashboard(started)
    assert captured["activation_started"] == started
    assert json.loads(captured["activation_snapshot"])["generated_at"] == started.isoformat()


def test_probe_identity_failure_rolls_back_pointer_and_stops_services(activation,monkeypatch):
    old,target="1"*40,"2"*40
    commands,metadata,switches=[],[],[]
    monkeypatch.setattr(sys,"argv",["activate","--target",target,"--expected-current",old,"--approve-host-change"])
    monkeypatch.setattr(activation,"inspect_release",lambda *a:None)
    def systemctl(*args):
        commands.append(args)
        if args[0]=="start":
            activation.HEALTH.write_text(json.dumps(valid_health(target)))
            activation.SNAPSHOT.write_text(json.dumps(valid_snapshot()))
    monkeypatch.setattr(activation,"systemctl",systemctl)
    monkeypatch.setattr(activation,"metadata",metadata.append)
    monkeypatch.setattr(activation.time,"sleep",lambda *_:None)
    def mismatch(*a,**k):
        raise ValueError("HOPE_UPGRADE_ACTIVATION_PRESENTATION_MISMATCH")
    monkeypatch.setattr(activation,"verify_dashboard",mismatch)
    def switch(root,sha,*,expected_current,before_switch,accept):
        before_switch();switches.append(sha)
        try: accept()
        except BaseException:
            switches.append(expected_current)
            raise
    monkeypatch.setattr(activation,"switch_release",switch)
    with pytest.raises(RuntimeError,match="ACCEPTANCE_TIMEOUT"):
        activation.main()
    assert switches==[target,old] and metadata==[target,old]
    assert commands[-1][0]=="stop"
