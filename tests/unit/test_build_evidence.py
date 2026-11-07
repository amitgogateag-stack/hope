from copy import deepcopy
from datetime import datetime,timezone,timedelta
import io
import json
import tarfile
import pytest
from hope.api import runtime_integrity as r,release_builder as b,acceptance_evidence as projection
from test_package_provenance import signed_runtime,SHA
from test_m3_dashboard_readiness import verified_runtime
from build_approval_fixture import sign


@pytest.mark.parametrize("fault",[None,"approval","inputs","sbom","archive","missing_signature","runtime","malicious"])
def test_runtime_requires_independent_build_approval_and_complete_sbom(signed_runtime,fault):
    root,env,mode,manifest,path=signed_runtime
    first=r.attest_runtime(root,SHA,mode)
    assert first["build_state"]=="VERIFIED"
    folder=root/"release-builds";prefix=SHA+"-"+mode
    if fault=="approval":(folder/(prefix+".json")).write_bytes(b'{"approved":true}')
    elif fault=="inputs":(folder/(prefix+".inputs.json")).write_bytes(b'{"builder":"self-approved"}')
    elif fault=="sbom":(folder/(prefix+".sbom.json")).write_bytes(b'{"components":[]}')
    elif fault=="archive":(folder/(prefix+".tar")).write_bytes(b"substitution")
    elif fault=="missing_signature":(folder/(prefix+".sig")).unlink()
    elif fault=="runtime":manifest["files"]["pyvenv.cfg"]="f"*64;r.manifest_path(root,SHA,mode).write_bytes(r._canonical(manifest))
    elif fault=="malicious":(folder/(prefix+".json")).write_bytes(b'{"schema":1,"schema":2}')
    result=r.diagnostic(root,SHA,mode,expected_manifest=r._digest(r._canonical(manifest)))
    assert result["state"]==("VERIFIED" if fault is None else "MISMATCH")
    assert str(root) not in json.dumps(result)


def test_tar_order_permissions_and_metadata_are_deterministic(tmp_path):
    files={"b.txt":(b"two",0o644),"a.txt":(b"one",0o755)}
    b._tar(tmp_path/"a.tar",files,1700000000);b._tar(tmp_path/"b.tar",dict(reversed(list(files.items()))),1700000000)
    assert (tmp_path/"a.tar").read_bytes()==(tmp_path/"b.tar").read_bytes()
    with tarfile.open(tmp_path/"a.tar") as archive:
        assert archive.getnames()==["a.txt","b.txt"]
        for member in archive:assert member.uid==member.gid==0 and member.uname==member.gname=="" and member.mtime==1700000000


@pytest.mark.parametrize("fault",[None,"expired","future","authority","secret","contradiction","malformed"])
def test_acceptance_dashboard_is_bounded_historical_lab_evidence(tmp_path,monkeypatch,fault):
    now=datetime.now(timezone.utc)
    value=dict(schema="hope.operational-acceptance.v1",scope="DISPOSABLE_LAB_ONLY",state="VERIFIED",release_commit="a"*40,
        generated_at=now.isoformat(),blockers=[],execution_authorized=False)
    if fault in {"expired","future"}:value["generated_at"]=(now+timedelta(seconds=1 if fault=="future" else -301)).isoformat()
    elif fault=="authority":value["execution_authorized"]=True
    elif fault=="secret":value["blockers"]=["NEVER_ECHO_PRIVATE_PATH"]
    elif fault=="contradiction":value["blockers"]=["REVIEW_DATABASE"]
    path=tmp_path/"evidence";path.write_bytes(b"{" if fault=="malformed" else r._canonical(value))
    monkeypatch.setattr(projection,"bounded_regular_read",lambda path,limit:path.read_bytes())
    result=projection.read_acceptance(path)
    assert result["state"]==("VERIFIED" if fault is None else "STALE" if fault=="expired" else "UNVERIFIED")
    assert result["execution_authorized"] is False and "NEVER_ECHO" not in str(result)


def test_builder_cli_failure_is_sanitized(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(b,"build_release",lambda **kw:(_ for _ in ()).throw(RuntimeError("private credential path")))
    args=[]
    for name in ("source","work","inputs","signature","public","python"):args.extend(("--"+name,str(tmp_path)))
    assert b.main(args+["--inputs-sha256","f"*64])==2
    result=json.loads(capsys.readouterr().out);assert result["state"]=="BLOCKED" and result["execution_authorized"] is False
