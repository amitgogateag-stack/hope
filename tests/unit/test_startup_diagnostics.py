from datetime import UTC,datetime,timedelta
import json
from unittest.mock import Mock
import pytest
from hope.api import paper_startup as p
from hope.infrastructure import paper_history

SHA='a'*40


@pytest.mark.parametrize('fault',[None,'extra','wrong_release','future','stale','dead_publisher','fabricated_success','oversize'])
def test_startup_diagnostics_never_promote_missing_or_contradictory_proof(tmp_path,monkeypatch,fault):
    now=datetime.now(UTC);path=tmp_path/'startup.json'
    raw=dict(schema='hope.paper-startup-observation.v1',state='BOOTSTRAPPED',release_commit=SHA,startup_authorization_sha256='b'*64,
        execution_authorization_sha256=None,generated_at=now.isoformat(),execution_authorized=False,publisher_instance='c'*32,publisher_identity_sha256='d'*64)
    if fault=='extra':raw['secret']='must not be exposed'
    elif fault=='wrong_release':raw['release_commit']='f'*40
    elif fault=='future':raw['generated_at']=(now+timedelta(seconds=1)).isoformat()
    elif fault=='stale':raw['generated_at']=(now-timedelta(seconds=6)).isoformat()
    elif fault=='fabricated_success':raw['state']='CYCLE_COMPLETED'
    path.write_text(json.dumps(raw) if fault!='oversize' else 'x'*4097);path.chmod(0o400)
    owner=Mock(return_value=dict(state='BLOCKED' if fault=='dead_publisher' else 'VERIFIED'))
    monkeypatch.setattr(paper_history,'publisher_observation',owner)
    result=p.startup_observation(path,SHA,now=now)
    assert result['state']==('BOOTSTRAPPED' if fault is None else 'STALE' if fault=='stale' else 'UNVERIFIED')
    assert result['execution_authorized'] is False and 'secret' not in json.dumps(result)
    if fault is None:owner.assert_called_once_with(path,expected_instance='c'*32,expected_identity='d'*64)


def test_missing_startup_observation_remains_unverified(tmp_path):
    assert p.startup_observation(tmp_path/'missing',SHA)['state']=='UNVERIFIED'
