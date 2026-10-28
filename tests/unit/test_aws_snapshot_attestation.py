"""Portable host orchestration with actual snapshot validation; no host calls."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest

from hope.api import aws_readiness as m
from hope.api.snapshot_contract import snapshot_evidence,verified_snapshot_payload,strict_json
from test_aws_readiness import EXPECTED,NOW,_units,_status,_paper_status,_dashboard_release,_snapshot


@pytest.fixture
def host(monkeypatch):
    root=Path("/isolated-host");release=root/"releases"/EXPECTED
    @contextmanager
    def guard(*a,**k):yield 123
    monkeypatch.setattr(m,"_deployment_root_identity",lambda root:(root,()))
    monkeypatch.setattr(m.operations_deployment,"_activation_guard",guard)
    monkeypatch.setattr(m,"_require_same_deployment_root",lambda *a:None)
    monkeypatch.setattr(m,"_deployed_release",lambda root:(release,EXPECTED,(),(),()))
    monkeypatch.setattr(m,"_require_same_release_storage",lambda *a:None)
    monkeypatch.setattr(m,"_deployed_metadata_snapshot",lambda fd:(f"HOPE_DEPLOYED_COMMIT={EXPECTED}\n".encode(),()))
    monkeypatch.setattr(m,"_require_same_deployed_metadata",lambda *a:None)
    snapshot=deepcopy(_snapshot()(None))
    # Leave snapshot_probe at its production default: baseline never calls it.
    monkeypatch.setattr(m,"_loopback_json_probe",lambda *a,**k:snapshot)
    def inspect(**kwargs):
        return m.inspect_aws_readiness(EXPECTED,root=root,now=NOW,
            systemd_probe=kwargs.pop("systemd_probe",_units()),status_probe=kwargs.pop("status_probe",_status()),
            paper_status_probe=_paper_status(),dashboard_release_probe=_dashboard_release(),
            release_probe=lambda *a:{"status":"COMPATIBLE_CODE"},unit_file_probe=lambda *a:True,**kwargs)
    return snapshot,inspect


@pytest.mark.parametrize("case,blocker",[
    ("halt","PAPER_DURABLE_HALT_OBSERVED"),("identity","SNAPSHOT_RELEASE_MISMATCH"),
    ("missing_commit","SNAPSHOT_RELEASE_MISMATCH"),("stale","SNAPSHOT_STALE"),
    ("future","SNAPSHOT_FROM_FUTURE"),("missing_recovery","SNAPSHOT_RECOVERY_UNVERIFIED"),
    ("generation","SNAPSHOT_STATUS_GENERATION_MISMATCH"),("stale_claim","PAPER_RECOVERY_REQUIRED"),
    ("effects","PAPER_CLAIMS_REQUIRE_REVIEW")])
def test_healthy_heartbeat_cannot_override_persisted_snapshot_blockers(host,case,blocker):
    snapshot,inspect=host
    if case=="halt":snapshot["paper"]["environment_state"]="HALTED"
    elif case=="identity":snapshot["deployed_commit"]="a"*40
    elif case=="missing_commit":snapshot.pop("deployed_commit")
    elif case=="stale":snapshot["generated_at"]=(NOW-timedelta(minutes=16)).isoformat()
    elif case=="future":snapshot["generated_at"]=(NOW+timedelta(seconds=1)).isoformat()
    elif case=="missing_recovery":snapshot["paper"].pop("recovery")
    elif case=="generation":snapshot["generated_at"]=(NOW-timedelta(minutes=1)).isoformat()
    else:
        snapshot["paper"]["job_runs"].update(total=1,claimed=1)
        snapshot["paper"]["recovery"].update(claimed_with_effects=case=="effects",
            claimed_without_effects=case!="effects",claims_older_than_one_hour=case=="stale_claim")
        snapshot["paper"]["recovery"]={k:int(v) for k,v in snapshot["paper"]["recovery"].items()}
    report=inspect()
    assert report["ready"] is False and blocker in report["blockers"]
    assert report["paper_status"]["state"]=="CURRENT"


def test_zero_jobs_and_no_trade_are_valid_observations_not_failure(host):
    snapshot,inspect=host
    snapshot["secret"]="NEVER_REPORT"
    report=inspect()
    assert report["ready"] is True
    assert report["snapshot"]["job_runs"]["total"]==0
    assert "NEVER_REPORT" not in json.dumps(report)


@pytest.mark.parametrize("change",["schema","source","counts","boolean","oversize","malformed"])
def test_corrupt_snapshot_fails_closed_with_fixed_diagnostic(host,change):
    snapshot,inspect=host
    if change=="schema":snapshot["schema_version"]=True
    elif change=="source":snapshot["source"]="invented"
    elif change=="counts":snapshot["paper"]["job_runs"]["total"]=1
    elif change=="boolean":snapshot["paper"]["recovery"]["claimed_with_effects"]=False
    elif change=="oversize":snapshot["private"]="secret"*6000
    else:snapshot["paper"]=None
    with pytest.raises(m.ReadinessError,match="^AWS_SNAPSHOT_DOCUMENT_INVALID$"):inspect()


def test_snapshot_uses_remaining_deadline_and_exhaustion_prevents_fetch(host):
    _,inspect=host;clock=[0.0];calls=[]
    def status(*a,**k):clock[0]=5;return _status()(*a,**k)
    with pytest.raises(m.ReadinessError,match="^AWS_SNAPSHOT_OBSERVATION_TIMEOUT$"):
        inspect(monotonic=lambda:clock[0],status_probe=status,snapshot_probe=lambda *a,**k:calls.append(True))
    assert calls==[]


def test_snapshot_cannot_reset_budget_used_by_prior_http_probes(host):
    snapshot,inspect=host;clock=[0.0];remaining=[]
    def status(*a,**k):clock[0]=4;return _status()(*a,**k)
    def read(*a,**k):remaining.append(k["timeout"]);return snapshot
    assert inspect(monotonic=lambda:clock[0],status_probe=status,snapshot_probe=read)["ready"] is True
    assert remaining==[1.0]


@pytest.mark.parametrize("stamp",[None,123,"bad","2026-10-24T22:55:00"])
def test_invalid_status_generation_cannot_authenticate_snapshot(host,stamp):
    _,inspect=host
    def status(*a,**k):return {**_status()(*a,**k),"snapshot_generated_at":stamp}
    report=inspect(status_probe=status)
    assert report["ready"] is False
    assert "SNAPSHOT_STATUS_GENERATION_INVALID" in report["blockers"]


def test_restart_during_snapshot_observation_invalidates_the_whole_window(host):
    snapshot,inspect=host;changed=[False];base=_units()
    def probe(*a,**k):
        values=base(*a,**k)
        if changed[0]:values["MainPID"]="9999"
        return values
    def read(*a,**k):changed[0]=True;return snapshot
    with pytest.raises(m.ReadinessError,match="^AWS_SYSTEMD_RUNTIME_CHANGED:"):
        inspect(systemd_probe=probe,snapshot_probe=read)


@pytest.mark.parametrize("body",['{"schema_version":1,"schema_version":1}',
    '{"nested":{"state":"HALTED","state":"RUNNING"}}','{"secret":NaN}',
    '{"secret":Infinity}','['*1100+']'*1100],ids=["duplicate","nested_duplicate","nan","infinity","depth"])
def test_ambiguous_or_unbounded_json_is_rejected_by_local_and_http_contract(body):
    with pytest.raises(ValueError):strict_json(body)
    with pytest.raises(ValueError):verified_snapshot_payload(body.encode(),now=NOW)
    with pytest.raises(m.ReadinessError,match="AWS_SNAPSHOT_DOCUMENT_INVALID"):
        m._snapshot_probe("http://127.0.0.1:8788/snapshot.json",capture=lambda *a,**k:body+"\n200")


def test_snapshot_http_transport_has_fixed_url_size_timeout_and_no_credentials():
    observed=[]
    def capture(command,**kwargs):
        observed.append((command,kwargs));return json.dumps(_snapshot()(None))+"\n200"
    assert m._snapshot_probe("http://127.0.0.1:8788/snapshot.json",timeout=2,capture=capture)["source"]=="hope_postgresql"
    command,options=observed[0]
    assert command[0]=="/usr/bin/curl" and "--noproxy" in command and "--location" not in command
    assert options["max_output"]==32772 and options["timeout"]==2
    assert "GH_TOKEN" not in options["env"]
    with pytest.raises(m.ReadinessError,match="URL_NOT_APPROVED"):
        m._snapshot_probe("http://public/snapshot.json",capture=capture)
