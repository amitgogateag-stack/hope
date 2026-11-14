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

@pytest.mark.parametrize('guarded',[False,True])
def test_competing_process_cannot_publish_failure_or_change_owner_evidence(tmp_path,guarded):
    from contextlib import nullcontext
    status=tmp_path/'health';path=Path(str(status)+'.history.json')
    with h.publisher_lease(status) as owner:
        history=h.History(path,lambda target,value:owner.publish(target,value,service.publish_service_status))
        history.check_lease=owner.check
        history.append(health('CURRENT'));owner.publish(status,health('CURRENT'),service.publish_service_status)
        before=(status.read_bytes(),path.read_bytes())
        code='''
from contextlib import nullcontext
import sys
from hope.infrastructure.paper_service import run_service,publish_startup_failure
args=dict(migrations_dir='unused',status_file=sys.argv[1],once=True)
if sys.argv[2]=='True':args['cycle_guard']=lambda:nullcontext(lambda:None)
assert run_service(None,None,**args)==1
assert publish_startup_failure(sys.argv[1],None) is False
'''
        subprocess.run([sys.executable,'-c',code,str(status),str(guarded)],check=True,capture_output=True,timeout=10)
        assert (status.read_bytes(),path.read_bytes())==before
        history.append(health('CURRENT'));owner.publish(status,health('STOPPED'),service.publish_service_status)
        assert len(h.read_history(path)['events'])==2
    with pytest.raises(RuntimeError):owner.publish(status,health(),service.publish_service_status)


def test_lost_owner_cannot_publish_status_or_history_after_successor(tmp_path):
    status=tmp_path/'health';lock=Path(str(status)+'.history.lock')
    with h.publisher_lease(status) as old:
        old.publish(status,health('CURRENT'),service.publish_service_status)
        lock.unlink()
        with h.publisher_lease(status) as successor:
            successor.publish(status,health('CURRENT'),service.publish_service_status)
            before=status.read_bytes()
            with pytest.raises(RuntimeError):old.publish(status,health('UNAVAILABLE'),service.publish_service_status)
            assert status.read_bytes()==before
    assert service.publish_startup_failure(status,None) is True
    assert json.loads(status.read_text())['state']=='UNAVAILABLE'


def test_publisher_evidence_binds_live_kernel_owner_and_health(tmp_path):
    status=tmp_path/'health'
    assert h.publisher_observation(status)['state']=='UNKNOWN'
    with h.publisher_lease(status) as owner:
        owner.publish(status,health('CURRENT'),service.publish_service_status)
        proof=h.publisher_observation(status)
        assert proof['state']=='UNVERIFIED' and len(proof['identity_sha256'])==64
        assert proof['lock_state']=='HELD' and proof['heartbeat_authenticated'] is False
        value=json.loads(status.read_text());value['publisher_instance']='0'*32
        service.publish_service_status(status,value)
        assert h.publisher_observation(status)['state']=='BLOCKED'
        owner.publish(status,health('CURRENT'),service.publish_service_status)
        assert h.publisher_observation(status)['state']=='UNVERIFIED'
    assert h.publisher_observation(status)['state']=='BLOCKED'


def test_current_protocol_two_health_cannot_survive_lease_release_as_ready(tmp_path):
    from hope.api.dashboard_server import _verified_operations
    status=tmp_path/'health';stamp=datetime.now(timezone.utc).isoformat()
    value=dict(schema_version=1,state='CURRENT',generated_at=stamp,last_success_at=stamp,
        cycles_succeeded=1,cycles_failed=0,jobs_executed=0,jobs_skipped=1,error_code=None,
        deployed_commit='a'*40,schema_compatibility='COMPATIBLE',runtime_gate='VERIFIED',paper_only=True,live_enabled=False)
    with h.publisher_lease(status) as owner:
        owner.publish(status,value,service.publish_service_status)
        assert json.loads(status.read_text())['schema_version']==2
        with pytest.raises(ValueError,match='PUBLISHER_UNVERIFIED'):_verified_operations(status)
    with pytest.raises(ValueError,match='PUBLISHER_UNVERIFIED'):_verified_operations(status)


def test_lost_service_lease_never_overwrites_successor_health(tmp_path,monkeypatch):
    from contextlib import nullcontext
    status=tmp_path/'health';lock=Path(str(status)+'.history.lock');successor_bytes=[]
    def replace_lease(*args,**kwargs):
        lock.unlink()
        with h.publisher_lease(status) as successor:
            successor.publish(status,dict(health('CURRENT'),schema_version=1),service.publish_service_status)
            successor_bytes.append(status.read_bytes())
        return 0,0
    monkeypatch.setattr(service,'run_service_cycle',replace_lease)
    result=service.run_service(None,None,migrations_dir=tmp_path,status_file=status,once=True,
        cycle_guard=lambda:nullcontext(lambda:None),notifier=Mock())
    assert result==1 and status.read_bytes()==successor_bytes[0]
    assert [event['state'] for event in h.read_history(Path(str(status)+'.history.json'))['events']]==['STARTING']


def test_unsafe_publisher_directory_and_unapproved_target_fail_without_writes(tmp_path):
    status=tmp_path/'health';tmp_path.chmod(0o777)
    try:
        assert service.publish_startup_failure(status,None) is False
        assert not status.exists()
    finally:tmp_path.chmod(0o700)
    with h.publisher_lease(status) as owner:
        with pytest.raises(RuntimeError,match='TARGET_INVALID'):
            owner.publish(tmp_path/'unapproved',health(),service.publish_service_status)
    assert not (tmp_path/'unapproved').exists()


def test_heartbeat_read_cannot_borrow_a_successor_lease_even_in_same_process(tmp_path,monkeypatch):
    from hope.api import dashboard_server
    status=tmp_path/'health';stamp=datetime.now(timezone.utc).isoformat()
    value=dict(schema_version=1,state='CURRENT',generated_at=stamp,last_success_at=stamp,
        cycles_succeeded=1,cycles_failed=0,jobs_executed=0,jobs_skipped=1,error_code=None,
        deployed_commit='a'*40,schema_compatibility='COMPATIBLE',runtime_gate='VERIFIED',paper_only=True,live_enabled=False)
    first_context=h.publisher_lease(status);first=first_context.__enter__();first.publish(status,value,service.publish_service_status)
    second_context=None;original=dashboard_server.bounded_regular_read
    def switch_after_read(path,limit):
        nonlocal second_context
        payload=original(path,limit)
        first_context.__exit__(None,None,None)
        second_context=h.publisher_lease(status);second=second_context.__enter__()
        second.publish(status,value,service.publish_service_status)
        return payload
    monkeypatch.setattr(dashboard_server,'bounded_regular_read',switch_after_read)
    try:
        with pytest.raises(ValueError,match='PUBLISHER_UNVERIFIED'):dashboard_server._verified_operations(status)
    finally:
        if second_context is not None:second_context.__exit__(None,None,None)


@pytest.mark.parametrize('schema',[1,2])
def test_same_uid_subprocess_cannot_turn_stale_heartbeat_current_under_genuine_lease(tmp_path,schema):
    import fcntl
    from hope.api.dashboard_server import _verified_operations
    from test_dashboard_m1_operations_surface import health as operations_health
    status=tmp_path/'health'
    stale=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    with h.publisher_lease(status) as owner:
        owner.publish(status,dict(operations_health(),generated_at=stale,last_success_at=stale),service.publish_service_status)
        before=json.loads(status.read_bytes())
        code='''
import os,sys,json
from pathlib import Path
from datetime import datetime,timezone
path=Path(sys.argv[1]);value=json.loads(path.read_bytes())
value.update(schema_version=int(sys.argv[2]),state='CURRENT',runtime_gate='VERIFIED',
    generated_at=datetime.now(timezone.utc).isoformat(),heartbeat_authenticated=True,publisher_verified=True)
value['last_success_at']=value['generated_at']
if value['schema_version']==1:
    value.pop('publisher_instance',None);value.pop('publisher_identity_sha256',None)
temporary=path.with_name(path.name+'.forged');temporary.write_text(json.dumps(value));temporary.chmod(0o600)
os.replace(temporary,path)
print(os.getuid(),flush=True)
'''
        result=subprocess.run([sys.executable,'-B','-c',code,str(status),str(schema)],
            check=True,capture_output=True,text=True,timeout=10)
        assert int(result.stdout.strip())==os.getuid()
        assert json.loads(status.read_bytes())['generated_at']>before['generated_at']
        owner.check()
        with Path(str(status)+'.history.lock').open('rb') as probe:
            with pytest.raises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_SH|fcntl.LOCK_NB)
        proof=h.publisher_observation(status)
        assert proof['state']!='VERIFIED' and proof['heartbeat_authenticated'] is False
        if schema==2:assert proof['state']=='UNVERIFIED' and proof['lock_state']=='HELD'
        with pytest.raises(ValueError,match='PUBLISHER_UNVERIFIED'):_verified_operations(status)
