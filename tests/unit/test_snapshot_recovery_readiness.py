"""Persisted recovery evidence must defeat an otherwise healthy heartbeat."""
from datetime import UTC,datetime
import json
import os
from threading import Thread
from urllib.request import ProxyHandler,build_opener

import pytest

from hope.api.dashboard_readiness import dashboard_readiness
from test_dashboard_m1_operations_surface import health
from test_dashboard_server import valid_snapshot
from hope.api.dashboard_server import DashboardHTTPServer
from hope.api.snapshot_contract import snapshot_evidence
from hope.api.m3_audit import _readiness


def test_stale_claim_without_effects_cannot_claim_ready(tmp_path,monkeypatch):
    if os.name=="nt":
        # Windows ACLs cannot model the POSIX mode contract; Linux CI uses the
        # real descriptor reader. Preserve the full payload/schema validation.
        monkeypatch.setattr("hope.api.dashboard_server.bounded_regular_read",lambda path,limit:path.read_bytes())
    snapshot=tmp_path/"snapshot.json";heartbeat=tmp_path/"health.json"
    data=json.loads(valid_snapshot());data["paper"].update(environment_state="RUNNING",
        job_runs=dict(total=1,claimed=1,succeeded=0,failed=0),recovery=dict(
            claimed_with_effects=0,claimed_without_effects=1,
            claims_older_than_one_hour=1,reconciliation_receipts=0))
    snapshot.write_text(json.dumps(data));heartbeat.write_text(json.dumps(health()))
    observed=dashboard_readiness(snapshot,heartbeat)
    assert observed["paper_health"]!="READY"
    assert observed["reconciliation"]=="RECOVERY_REQUIRED"
    assert observed["execution_authorized"] is False


@pytest.mark.parametrize("halted",[False,True])
def test_live_http_preserves_recovery_diagnosis_and_durable_halt(tmp_path,monkeypatch,halted):
    if os.name=="nt":
        monkeypatch.setattr("hope.api.dashboard_server.bounded_regular_read",lambda path,limit:path.read_bytes())
    snapshot=tmp_path/"snapshot.json";heartbeat=tmp_path/"health.json";page=tmp_path/"page.html"
    page.write_text("HOPE");commit="a"*40
    data=json.loads(valid_snapshot());data["deployed_commit"]=commit
    data["paper"].update(environment_state="HALTED" if halted else "RUNNING",
        job_runs=dict(total=1,claimed=1,succeeded=0,failed=0),recovery=dict(
            claimed_with_effects=0,claimed_without_effects=1,
            claims_older_than_one_hour=1,reconciliation_receipts=0))
    snapshot.write_text(json.dumps(data));heartbeat.write_text(json.dumps(health()))
    opener=build_opener(ProxyHandler({}))
    with DashboardHTTPServer(0,page,snapshot,heartbeat,deployed_commit=commit) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with opener.open(base+"/status.json",timeout=3) as response:status=json.loads(response.read())
            with opener.open(base+"/snapshot.json",timeout=3) as response:payload=response.read()
            with opener.open(base+"/readiness.json",timeout=3) as response:readiness=json.loads(response.read())
            evidence,blockers=snapshot_evidence(payload,expected_commit=commit,
                observed_at=datetime.now(UTC),status_timestamp=status["snapshot_generated_at"])
            assert "PAPER_RECOVERY_REQUIRED" in blockers
            assert evidence["reconciliation"]=="RECOVERY_REQUIRED"
            assert _readiness(readiness)["reconciliation"]=="RECOVERY_REQUIRED"
            assert readiness["paper_health"]==("HALTED" if halted else "UNAVAILABLE")
            assert readiness["execution_authorized"] is False
        finally:server.shutdown();thread.join(timeout=5)
