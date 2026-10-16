import importlib.util
import json
from pathlib import Path
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
    monkeypatch.setattr(module.os, "geteuid", lambda: 0, raising=False)
    return module


@pytest.mark.parametrize("failure", ["preflight", "start", "timeout", None])
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


def test_metadata_is_atomically_published_with_no_secret_or_extra_fields(activation):
    activation.metadata("a" * 40)
    assert (activation.ROOT / "deployed.env").read_text() == "HOPE_DEPLOYED_COMMIT=" + "a" * 40 + "\n"
    assert sorted(p.name for p in activation.ROOT.iterdir()) == ["deployed.env"]


def valid_health(target):
    timestamp = datetime.now(UTC).isoformat()
    return dict(deployed_commit=target, generated_at=timestamp, last_success_at=timestamp,
                state="CURRENT", schema_compatibility="COMPATIBLE", paper_only=True, live_enabled=False)


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
