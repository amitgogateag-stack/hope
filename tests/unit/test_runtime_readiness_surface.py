"""Runtime observations cannot manufacture dashboard or host readiness."""
import json
from threading import Thread
from urllib.request import ProxyHandler,build_opener
from urllib.error import HTTPError

import pytest

from hope.api import aws_readiness as aws
from hope.api import dashboard_activation as activation
from hope.api.dashboard_server import DashboardHTTPServer
from hope.api.runtime_integrity import unverified
from test_dashboard_server import valid_snapshot
from test_dashboard_m1_operations_surface import health
from test_dashboard_only_activation import setup
from test_activation_guard import guard_platform
from test_aws_snapshot_attestation import host
from test_aws_readiness import EXPECTED,_runtime


@pytest.mark.parametrize("state",["VERIFIED","UNVERIFIED","MISMATCH","BLOCKED","wrong_release","private_payload"])
def test_runtime_endpoint_is_bounded_and_mandatory_for_ready(setup,state):
    layout,_=setup;commit="a"*40
    data=json.loads(valid_snapshot());data["deployed_commit"]=commit;data["paper"]["environment_state"]="RUNNING"
    layout.snapshot.write_text(json.dumps(data));layout.health.write_text(json.dumps(health()))
    page=layout.root/"page.html";page.write_text("HOPE")
    evidence=dict(schema="hope.runtime-integrity.v1",state="VERIFIED",code="MANIFEST_AND_RUNTIME_MATCH",
        release_commit=commit,runtime_mode="shared",manifest_sha256="b"*64,inventory_sha256="c"*64,process_sha256=None,file_count=1,execution_authorized=False)
    if state in {"UNVERIFIED","MISMATCH","BLOCKED"}:
        evidence={**unverified(),"state":state}
    elif state=="wrong_release":evidence["release_commit"]="c"*40
    elif state=="private_payload":evidence["private_path"]="/secret/credential"
    opener=build_opener(ProxyHandler({}))
    with DashboardHTTPServer(0,page,layout.snapshot,layout.health,deployed_commit=commit,
            activation_check=lambda:{"runtime_integrity":evidence}) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with opener.open(base+"/runtime.json",timeout=3) as response:
                body=response.read();observed=json.loads(body)
            assert len(body)<1024 and b"secret" not in body and b"credential" not in body
            assert observed["execution_authorized"] is False
            if state in {"wrong_release","private_payload"}:
                assert observed["state"]=="BLOCKED"
                with pytest.raises(HTTPError) as failed:opener.open(base+"/readiness.json",timeout=3)
                assert failed.value.code==503;failed.value.close()
            else:
                with opener.open(base+"/readiness.json",timeout=3) as response:ready=json.loads(response.read())
                assert ready["runtime_integrity"]["state"]==state
                assert ready["paper_health"]==("READY" if state=="VERIFIED" else "UNAVAILABLE")
                if state!="VERIFIED":assert ready["reason"]=="RUNTIME_"+state
        finally:server.shutdown();thread.join(timeout=5)


def test_blocked_activation_can_only_return_sanitized_runtime_diagnostic(setup):
    layout,_=setup;page=layout.root/"page";page.write_text("HOPE")
    def blocked():raise RuntimeError("/private/path credential=secret")
    opener=build_opener(ProxyHandler({}))
    with DashboardHTTPServer(0,page,layout.snapshot,activation_check=blocked) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with opener.open(f"http://127.0.0.1:{server.server_port}/runtime.json",timeout=3) as response:body=response.read()
            assert json.loads(body)["state"]=="BLOCKED" and b"secret" not in body and b"private" not in body
        finally:server.shutdown();thread.join(timeout=5)


@pytest.mark.parametrize("state",["UNVERIFIED","MISMATCH","BLOCKED"])
def test_host_cannot_be_ready_without_independent_runtime_evidence(host,state):
    _,inspect=host
    evidence={**unverified(),"state":state}
    result=inspect(runtime_probe=lambda *a,**k:evidence)
    assert result["ready"] is False and "RUNTIME_"+state in result["blockers"]


def test_host_rejects_verified_runtime_for_a_different_release(host):
    _,inspect=host;result=inspect(runtime_probe=_runtime("c"*40))
    assert result["ready"] is False and "RUNTIME_RELEASE_MISMATCH" in result["blockers"]


def test_default_host_runtime_probe_never_adopts_a_missing_journal_as_verified(tmp_path):
    result=aws._runtime_probe(tmp_path,EXPECTED,{"MainPID":"123"},timeout=1)
    assert result["state"]=="UNVERIFIED" and result["execution_authorized"] is False


@pytest.mark.parametrize("changed",[None,"journal","pid","public","other_pid","command_failure"])
def test_independent_host_attestation_binds_manifest_process_listener_and_journal(tmp_path,monkeypatch,changed):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import subprocess
    record=dict(schema="hope.dashboard-activation.v3",phase="COMMITTED",target=EXPECTED,
        shared=EXPECTED,runtime="release",runtime_manifest="a"*64)
    observed=[False]
    monkeypatch.setattr(activation,"Layout",lambda root:SimpleNamespace(root=root))
    monkeypatch.setattr(activation,"_record",lambda layout:
        {**record,"phase":"ROLLBACK_FAILED"} if changed=="journal" and observed[0] else record)
    monkeypatch.setattr(activation,"_pointer",lambda *a,**k:EXPECTED)
    monkeypatch.setattr(aws,"attest_runtime",lambda *a,**k:_runtime()())
    monkeypatch.setattr(aws.operations_deployment,"_process_identity",lambda pid:
        ("boot","20" if changed=="pid" and observed[0] else "10"))
    verifier=Mock();monkeypatch.setattr(aws,"verify_listener",verifier)
    def capture(command,**kwargs):
        assert command==["/usr/bin/ss","-H","-ltnp","sport = :8788"]
        assert kwargs["max_output"]==4096 and 0<kwargs["timeout"]<=2
        observed[0]=True
        if changed=="command_failure":raise subprocess.CalledProcessError(1,command,output="secret")
        address="0.0.0.0" if changed=="public" else "127.0.0.1"
        pid="999" if changed=="other_pid" else "4321"
        return f'LISTEN 0 128 {address}:8788 0.0.0.0:* users:(("python",pid={pid},fd=3))'
    monkeypatch.setattr(aws,"bounded_capture",capture)
    result=aws._runtime_probe(tmp_path,EXPECTED,{"MainPID":"4321"},timeout=2)
    assert result["state"]==("VERIFIED" if changed is None else "BLOCKED")
    if changed is None:assert len(result["process_sha256"])==64
    assert "secret" not in json.dumps(result)
    assert verifier.call_args.kwargs["expected_identity"]==("boot","10")


@pytest.mark.parametrize("changed",["inventory_sha256","process_sha256"])
def test_runtime_replacement_after_http_or_release_authentication_invalidates_whole_report(host,changed):
    from unittest.mock import Mock
    _,inspect=host;first=_runtime()();second={**first,changed:"e"*64}
    probe=Mock(side_effect=[first,second])
    with pytest.raises(aws.ReadinessError,match="^AWS_RUNTIME_EVIDENCE_CHANGED$"):
        inspect(runtime_probe=probe)
