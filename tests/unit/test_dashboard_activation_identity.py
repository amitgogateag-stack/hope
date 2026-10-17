"""Strict release presentation identity, including a real loopback server."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from threading import Thread

import pytest

from test_dashboard_upgrade_probe import FakeOpener, fixture_responses, valid_paper
from hope.api.dashboard_server import DashboardHTTPServer
from hope.api.dashboard_upgrade_probe import verify_dashboard


def responses_for_activation():
    started=datetime.now(timezone.utc)-timedelta(seconds=1)
    paper=valid_paper()
    responses=fixture_responses()
    responses["/snapshot.json"]=(200,json.dumps(paper).encode())
    responses["/status.json"]=(200,json.dumps(dict(schema_version=1,service="RUNNING",data_feed="CURRENT",snapshot_generated_at=paper["generated_at"])).encode())
    return started,paper,responses


def test_strict_probe_accepts_exact_post_switch_presentation():
    started,paper,responses=responses_for_activation()
    assert verify_dashboard(opener=FakeOpener(responses),activation_started=started,
        activation_snapshot=json.dumps(paper).encode())["paper_feed"]=="AVAILABLE"


@pytest.mark.parametrize("change", ["stale_health","unavailable_health","health_identity","snapshot_identity","same_timestamp_different_counts","missing_snapshot"])
def test_strict_probe_rejects_valid_but_unmatched_resources(change):
    started,paper,responses=responses_for_activation()
    health=json.loads(responses["/status.json"][1]);served=deepcopy(paper)
    if change=="stale_health": health["data_feed"]="STALE"
    elif change=="unavailable_health": health.update(data_feed="UNAVAILABLE",snapshot_generated_at=None)
    elif change=="health_identity": health["snapshot_generated_at"]=(started-timedelta(minutes=1)).isoformat()
    elif change=="snapshot_identity": served["generated_at"]=(started-timedelta(minutes=1)).isoformat()
    elif change=="same_timestamp_different_counts": served["research"]["run_count"]+=1
    responses["/status.json"]=(200,json.dumps(health).encode())
    responses["/snapshot.json"]=(503,b"Verified snapshot unavailable") if change=="missing_snapshot" else (200,json.dumps(served).encode())
    with pytest.raises(ValueError,match="ACTIVATION_PRESENTATION_MISMATCH"):
        verify_dashboard(opener=FakeOpener(responses),activation_started=started,activation_snapshot=json.dumps(paper).encode())


@pytest.mark.parametrize("kind", ["missing_time","naive_time","missing_snapshot","not_bytes","oversized","old","future"])
def test_invalid_activation_selection_is_rejected_before_network(kind):
    started,paper,_=responses_for_activation()
    payload=json.dumps(paper).encode()
    if kind=="missing_time": started=None
    elif kind=="naive_time": started=started.replace(tzinfo=None)
    elif kind=="missing_snapshot": payload=None
    elif kind=="not_bytes": payload="invalid"
    elif kind=="oversized": payload=b" "*32769
    else:
        paper["generated_at"]=(started+timedelta(seconds=-1 if kind=="old" else 240)).isoformat()
        payload=json.dumps(paper).encode()
    opener=FakeOpener()
    with pytest.raises(ValueError,match="ACTIVATION_"):
        verify_dashboard(opener=opener,activation_started=started,activation_snapshot=payload)
    assert opener.calls==0


def test_strict_probe_against_real_loopback_snapshot_and_replacement(tmp_path):
    started,paper,_=responses_for_activation()
    snapshot=tmp_path/"snapshot.json";preview=tmp_path/"index.html"
    snapshot.write_text(json.dumps(paper));preview.write_text('<div id="legacy-experiments" id="legacy-strategies" id="legacy-datasets">HOPE dashboard v0.5</div>')
    with DashboardHTTPServer(0,preview,snapshot) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        expected=json.dumps(paper).encode()
        try:
            assert verify_dashboard(base,activation_started=started,activation_snapshot=expected)["paper_feed"]=="AVAILABLE"
            paper["research"]["run_count"]+=1;snapshot.write_text(json.dumps(paper))
            with pytest.raises(ValueError,match="ACTIVATION_PRESENTATION_MISMATCH"):
                verify_dashboard(base,activation_started=started,activation_snapshot=expected)
            assert verify_dashboard(base,activation_started=started,activation_snapshot=json.dumps(paper).encode())["paper_feed"]=="AVAILABLE"
        finally:
            server.shutdown();thread.join(timeout=5)
