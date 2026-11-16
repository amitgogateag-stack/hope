"""Content-authentication logic; only the native file-read boundary is modeled.

Separate POSIX subprocess tests exercise genuine file replacement and flock.
No positive independent publisher authority is fabricated by this module.
"""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from hope.api import dashboard_server as d
from hope.infrastructure import paper_history as h
from test_dashboard_m1_operations_surface import health


@pytest.mark.parametrize('schema',[1,2])
@pytest.mark.parametrize('claim',[None,False,True])
def test_unsigned_current_cannot_borrow_legacy_schema_or_self_authentication(monkeypatch,schema,claim):
    value=dict(health(),schema_version=schema,publisher_instance='c'*32,publisher_identity_sha256='d'*64)
    if claim is not None:value['heartbeat_authenticated']=claim
    monkeypatch.setattr(d,'bounded_regular_read',lambda *_:json.dumps(value).encode())
    observer=Mock(return_value={'state':'VERIFIED','heartbeat_authenticated':False})
    monkeypatch.setattr(h,'publisher_observation',observer)
    with pytest.raises(ValueError,match='PUBLISHER_UNVERIFIED'):d._verified_operations(Path('unused'))
    if schema==1:observer.assert_not_called()
    else:observer.assert_called_once()


@pytest.mark.parametrize('schema',[1,2])
@pytest.mark.parametrize('state',['STARTING','HALTED','UNAVAILABLE','STOPPED'])
def test_noncurrent_diagnostics_remain_available_without_verified_runtime_claim(monkeypatch,schema,state):
    value=dict(health(),schema_version=schema,state=state,publisher_instance='c'*32,publisher_identity_sha256='d'*64)
    monkeypatch.setattr(d,'bounded_regular_read',lambda *_:json.dumps(value).encode())
    observer=Mock(side_effect=AssertionError('diagnostic is not positive admission'))
    monkeypatch.setattr(h,'publisher_observation',observer)
    safe=json.loads(d._verified_operations(Path('unused')))
    assert safe['state']==state and safe['runtime_gate']=='UNVERIFIED'
    assert safe['paper_only'] is True and safe['live_enabled'] is False
    observer.assert_not_called()


def test_metadata_snapshot_and_runtime_cannot_promote_unsigned_current_to_ready(monkeypatch):
    from datetime import datetime,timezone
    from hope.api import dashboard_readiness as readiness
    from test_dashboard_server import valid_snapshot
    from test_m3_dashboard_readiness import verified_runtime
    value=dict(health(),heartbeat_authenticated=True)
    monkeypatch.setattr(d,'bounded_regular_read',lambda *_:json.dumps(value).encode())
    monkeypatch.setattr(readiness,'_verified_snapshot',lambda *_:(valid_snapshot(),datetime.now(timezone.utc)))
    result=readiness.dashboard_readiness(Path('snapshot'),Path('health'),runtime_evidence=verified_runtime())
    assert result['paper_health']=='UNAVAILABLE' and result['reason']=='HEALTH_INVALID'
    assert result['execution_authorized'] is False


def test_authenticated_heartbeat_cannot_promote_unauthenticated_snapshot(monkeypatch):
    from datetime import datetime,timezone
    from hope.api import dashboard_readiness as readiness
    from test_dashboard_server import valid_snapshot
    from test_m3_dashboard_readiness import verified_runtime
    safe=dict(health(),runtime_gate="VERIFIED")
    generated=datetime.now(timezone.utc)
    snapshot=json.loads(valid_snapshot());snapshot['paper']['environment_state']='RUNNING'
    monkeypatch.setattr(readiness,'_verified_operations',lambda *_:json.dumps(safe).encode())
    monkeypatch.setattr(readiness,'_verified_snapshot',lambda *_:(json.dumps(snapshot).encode(),generated))
    result=readiness.dashboard_readiness(
        Path('snapshot'),Path('health'),runtime_evidence=verified_runtime(),
    )
    assert result['paper_health']=='UNAVAILABLE'
    assert result['reason']=='SNAPSHOT_PUBLISHER_UNVERIFIED'
    assert result['database']=='UNVERIFIED'
    assert result['reconciliation']=='UNVERIFIED'
    assert result['execution_authorized'] is False
