from datetime import datetime,timezone,timedelta
import hashlib
import json
from unittest.mock import Mock

import pytest
from hope.api import rehearsal_evidence as m
from test_m3_dashboard_readiness import verified_runtime


@pytest.mark.parametrize("case",["valid","stale","future","missing","malformed","scope","authority","identity","provenance","private"])
def test_only_authenticated_lab_observations_are_published(tmp_path,case):
    path=tmp_path/"result.json";now=datetime.now(timezone.utc)
    value=dict(schema="hope.deployment-rehearsal.v1",scope="DISPOSABLE_LAB_ONLY",state="VERIFIED",release_commit="a"*40,
        generated_at=now.isoformat(),runtime_integrity=verified_runtime(),execution_authorized=False,host_changes=False)
    if case=="stale":value["generated_at"]=(now-timedelta(days=2)).isoformat()
    if case=="future":value["generated_at"]=(now+timedelta(days=2)).isoformat()
    if case=="scope":value["scope"]="PRODUCTION"
    if case=="authority":value["execution_authorized"]=True
    if case=="identity":value["release_commit"]="b"*40
    if case=="provenance":value["runtime_integrity"].update(provenance_state="UNVERIFIED",provenance_sha256=None)
    if case=="private":value["secret"]="NEVER_EXPOSE"
    if case!="missing":path.write_text("{" if case=="malformed" else json.dumps(value))
    expected="0"*64 if case=="missing" else hashlib.sha256(path.read_bytes()).hexdigest()
    result=m.read_rehearsal(path,expected_sha256=expected)
    assert result["state"]==("VERIFIED" if case in {"valid","private"} else "STALE" if case=="stale" else "UNVERIFIED")
    assert result["execution_authorized"] is False and "NEVER_EXPOSE" not in str(result)


def test_rehearsal_requires_independently_supplied_digest(tmp_path,monkeypatch):
    path=tmp_path/"rehearsal-result.json"
    path.write_text(json.dumps(dict(schema="hope.deployment-rehearsal.v1",scope="DISPOSABLE_LAB_ONLY",
        state="VERIFIED",release_commit="a"*40,generated_at=datetime.now(timezone.utc).isoformat(),
        runtime_integrity=verified_runtime(),execution_authorized=False,host_changes=False)))
    reader=Mock();monkeypatch.setattr(m,"authenticated_bounded_regular_read",reader)
    result=m.read_rehearsal(path)
    assert result["state"]=="UNVERIFIED" and result["execution_authorized"] is False
    reader.assert_not_called()


def test_rehearsal_rejects_digest_mismatch(tmp_path):
    path=tmp_path/"rehearsal-result.json"
    path.write_text(json.dumps(dict(schema="hope.deployment-rehearsal.v1",scope="DISPOSABLE_LAB_ONLY",
        state="VERIFIED",release_commit="a"*40,generated_at=datetime.now(timezone.utc).isoformat(),
        runtime_integrity=verified_runtime(),execution_authorized=False,host_changes=False)))
    result=m.read_rehearsal(path,expected_sha256="0"*64)
    assert result["state"]=="UNVERIFIED" and result["execution_authorized"] is False
