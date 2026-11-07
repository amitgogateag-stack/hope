from datetime import datetime,timezone,timedelta
import json

import pytest
from hope.api import rehearsal_evidence as m
from test_m3_dashboard_readiness import verified_runtime


@pytest.mark.parametrize("case",["valid","stale","future","missing","malformed","scope","authority","identity","provenance","private"])
def test_only_bounded_lab_observations_are_published(tmp_path,monkeypatch,case):
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
    # Native no-follow/ACL storage is covered by the shared durable reader.
    monkeypatch.setattr(m,"bounded_regular_read",lambda p,limit:p.read_bytes() if p.stat().st_size<=limit else b"{")
    result=m.read_rehearsal(path)
    assert result["state"]==("VERIFIED" if case in {"valid","private"} else "STALE" if case=="stale" else "UNVERIFIED")
    assert result["execution_authorized"] is False and "NEVER_EXPOSE" not in str(result)
