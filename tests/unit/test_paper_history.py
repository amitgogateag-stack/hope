"""Real durability, lease exclusion, interrupted publication and corrupt history."""
from datetime import datetime,timezone,timedelta
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
from hope.infrastructure import paper_history as h,paper_service as service

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Native POSIX durability and flock exercised in Linux CI')


def health(state='STARTING',code=None):return dict(generated_at=datetime.now(timezone.utc).isoformat(),state=state,error_code=code)


def test_restart_bounded_window_chain_corruption_and_expiry(tmp_path):
    status=tmp_path/'health';path=Path(str(status)+'.history.json')
    for iteration in range(4):
        with h.history_lease(status,service.publish_service_status) as history:
            history.append(health())
            for _ in range(10):history.append(health('CURRENT'))
            history.append(health('STOPPED'))
    value=h.read_history(path);assert len(value['events'])==32 and value['events'][-1]['sequence']==48
    assert value['anchor']!='0'*64 and len({v['instance'] for v in value['events']})==3
    result=h.history_observation(path);assert result['state']=='OBSERVED' and result['retained_starts']==2
    assert h.history_observation(path,now=datetime.now(timezone.utc)+timedelta(seconds=301))['state']=='STALE'
    value['events'][-2]['state']='HALTED';service.publish_service_status(path,value)
    assert h.history_observation(path)['state']=='UNVERIFIED'
    with pytest.raises(RuntimeError):
        with h.history_lease(status,service.publish_service_status):pass


def test_concurrent_instance_and_lock_replacement_block_before_work(tmp_path):
    status=tmp_path/'health'
    with h.history_lease(status,service.publish_service_status) as first:
        first.append(health())
        with pytest.raises(RuntimeError,match='CONCURRENT'):
            with h.history_lease(status,service.publish_service_status):pytest.fail('competing worker admitted')
        Path(str(status)+'.history.lock').unlink()
        Path(str(status)+'.history.lock').write_bytes(b'')
        with pytest.raises(RuntimeError,match='UNVERIFIED'):first.check()


def test_failed_publication_retains_previous_whole_history_and_blocks(tmp_path):
    status=tmp_path/'health'
    with h.history_lease(status,service.publish_service_status) as history:
        history.append(health());before=history.path.read_bytes()
        history.publisher=Mock(side_effect=OSError('injected fsync failure'))
        with pytest.raises(OSError):history.append(health('CURRENT'))
        assert history.path.read_bytes()==before
        assert h.read_history(history.path)==history.value
        history.path.unlink()
        with pytest.raises(FileNotFoundError):history.check()


@pytest.mark.parametrize('fault',['corrupt','symlink','sequence','chain','future','deleted'])
def test_corrupt_or_replaced_history_never_loads_factory(tmp_path,monkeypatch,fault):
    status=tmp_path/'health';path=Path(str(status)+'.history.json')
    with h.history_lease(status,service.publish_service_status) as history:history.append(health())
    value=h.read_history(path)
    if fault=='corrupt':path.write_bytes(b'{')
    elif fault=='symlink':
        other=tmp_path/'other';path.rename(other);path.symlink_to(other)
    elif fault=='deleted':
        # Deletion after startup is detected before factory, rather than treating
        # a missing legacy history on initial adoption as proof of corruption.
        pass
    elif fault=='future':
        value['events'][0]['generated_at']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        event=value['events'][0];event['sha256']=h.digest({k:v for k,v in event.items() if k!='sha256'})
        service.publish_service_status(path,value)
    else:
        value['events'][0]['sequence']=False if fault=='sequence' else 2
        service.publish_service_status(path,value)
    @contextmanager
    def guard():
        if fault=='deleted':path.unlink()
        yield lambda:None
    cycle=Mock();monkeypatch.setattr(service,'run_service_cycle',cycle)
    assert service.run_service(None,None,migrations_dir=tmp_path,status_file=status,once=True,cycle_guard=guard,notifier=Mock())==1
    cycle.assert_not_called()
    assert json.loads(status.read_text())['runtime_gate']=='UNVERIFIED'


def test_sigkill_releases_lease_and_next_instance_preserves_prior_window(tmp_path):
    status=tmp_path/'health';path=Path(str(status)+'.history.json')
    code='''
import sys,time
from datetime import datetime,timezone
from hope.infrastructure.paper_history import history_lease
from hope.infrastructure.paper_service import publish_service_status
with history_lease(sys.argv[1],publish_service_status) as h:
 h.append(dict(generated_at=datetime.now(timezone.utc).isoformat(),state='STARTING',error_code=None))
 print('PUBLISHED',flush=True)
 time.sleep(60)
'''
    process=subprocess.Popen([sys.executable,'-c',code,str(status)],stdout=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='PUBLISHED'
        process.kill();process.wait(5)
        with h.history_lease(status,service.publish_service_status) as history:
            history.append(health());history.append(health('CURRENT'))
        result=h.read_history(path);assert [e['state'] for e in result['events']]==['STARTING','STARTING','CURRENT']
        assert result['events'][0]['instance']!=result['events'][1]['instance']
    finally:
        if process.poll() is None:process.kill();process.wait(5)
