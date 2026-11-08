"""Staging is non-executing evidence, never running PAPER readiness."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from hope.api import paper_stage as s,paper_bootstrap as bootstrap,deployment_contract as d,paper_controller as c
from hope.infrastructure.paper_history import publisher_observation
from test_deployment_contract import contract,SHA
from test_paper_bootstrap import launch

REQUEST='a'*64
FACTORY='approved.factory:build'
NATIVE=pytest.mark.skipif(os.name!='posix',reason='Linux stage publisher/process contract')


@pytest.mark.parametrize('fault',[None,'stage_changed','stage_missing','stage_stale','provider','image','runtime','receipt','signature','revoked'])
def test_staged_receipt_reuses_all_independent_gates_without_claiming_running(contract,monkeypatch,fault):
    root,receipt,pin,image,host,container,probes=contract
    stage=dict(binding=('kernel','nonce'),expires_at=probes['now']+4)
    collect=Mock(side_effect=lambda *a,**k:deepcopy(stage));monkeypatch.setattr(s,'staged_process_observation',collect)
    readiness=probes.pop('host_readiness')
    if fault=='stage_changed':collect.side_effect=[dict(stage),dict(stage,binding=('other','nonce'))]
    elif fault=='stage_missing':collect.side_effect=FileNotFoundError('private path')
    elif fault=='stage_stale':stage['expires_at']=probes['now']
    elif fault=='provider':monkeypatch.setattr(d,'provider_contract',Mock(side_effect=RuntimeError('private')))
    elif fault=='image':image['scope']='DISPOSABLE_LAB_ONLY'
    elif fault=='runtime':probes['runtime_probe'].return_value['manifest_sha256']='0'*64
    elif fault=='receipt':receipt['expires_at']=probes['now'];pin()
    elif fault=='signature':monkeypatch.setattr(d,'signed',Mock(side_effect=RuntimeError('private signature')))
    elif fault=='revoked':monkeypatch.setattr(d,'authorities',Mock(side_effect=RuntimeError('revoked private role')))
    result=d.inspect_staged_deployment(root,SHA,**probes)
    assert result['schema']=='hope.staged-deployment-observation.v1'
    assert result['state']==('STAGED' if fault is None else 'BLOCKED')
    assert result['execution_authorized'] is False and 'private' not in json.dumps(result)
    readiness.assert_not_called()
    if fault is None:
        assert result['expires_at']==probes['now']+4
        with pytest.raises(RuntimeError):c._proof(result,c.plan(SHA,'b'*64,FACTORY),probes['now'])
        # Running readiness remains mandatory, even with perfect stage proof.
        readiness.return_value['ready']=False
        assert d.inspect_deployment(root,SHA,host_readiness=readiness,**probes)['state']=='BLOCKED'


@pytest.mark.parametrize('fault',['manifest','request','timeout','factory','frozen'])
def test_canonical_stage_argv_rejects_unsafe_inputs(fault):
    sha=SHA;manifest='b'*64;request=REQUEST;timeout=300;factory=FACTORY
    if fault=='manifest':manifest='x'
    elif fault=='request':request='../private'
    elif fault=='timeout':timeout=301
    elif fault=='factory':factory='private:run;unsafe'
    else:sha='7e9d341742818a97accce3e2301b1c7f96596795'
    with pytest.raises(RuntimeError):s.arguments(sha,manifest,factory,request,timeout)


@pytest.mark.parametrize('staged',[False,True])
@pytest.mark.parametrize('clock_fault',['expiry','reversal'])
def test_slow_closing_checks_cannot_outlive_approval_or_reverse_clock(contract,monkeypatch,staged,clock_fault):
    root,receipt,pin,image,host,container,probes=contract;now=probes.pop('now')
    ticks=iter([now,now,now+101 if clock_fault=='expiry' else now-1])
    monkeypatch.setattr(d.time,'time',lambda:next(ticks))
    monkeypatch.setattr(s,'staged_process_observation',Mock(return_value=dict(binding=('same',),expires_at=now+200)))
    if staged:probes.pop('host_readiness')
    result=(d.inspect_staged_deployment if staged else d.inspect_deployment)(root,SHA,**probes)
    assert result['state']=='BLOCKED' and result['execution_authorized'] is False


@pytest.mark.parametrize('fault',['interrupted','corrupt','changed','stale_full_activation','unreadable_full_activation'])
def test_staged_inspection_never_bypasses_activation_recovery(contract,monkeypatch,fault):
    root,receipt,pin,image,host,container,probes=contract;probes.pop('host_readiness')
    monkeypatch.setattr(s,'staged_process_observation',Mock(return_value=dict(binding=('same',),expires_at=probes['now']+4)))
    if fault in {'stale_full_activation','unreadable_full_activation'}:
        from hope.api import operations_deployment
        helper=Mock(return_value={'state':'RECOVERY_REQUIRED'}) if fault=='stale_full_activation' else Mock(side_effect=PermissionError('private lock'))
        monkeypatch.setattr(operations_deployment,'inspect_activation_recovery',helper)
    elif fault=='changed':monkeypatch.setattr(s,'activation_snapshot',Mock(side_effect=[None,{'phase':'COMMITTED'}]))
    else:
        from hope.api import dashboard_activation
        if fault=='interrupted':monkeypatch.setattr(dashboard_activation,'_record',Mock(return_value={'phase':'PREPARED'}))
        else:monkeypatch.setattr(dashboard_activation,'_record',Mock(side_effect=RuntimeError('private corrupt journal')))
    report=d.inspect_staged_deployment(root,SHA,**probes)
    assert report['state']=='BLOCKED' and 'private' not in json.dumps(report)


@pytest.mark.parametrize('phase',['os','pinned'])
def test_stage_bootstrap_never_dispatches_worker_or_forwards_database_credentials(launch,monkeypatch,phase):
    release,authenticate=launch
    binary,evidence=authenticate.return_value
    authenticate.return_value=(binary,dict(evidence,state='VERIFIED',provenance_state='VERIFIED',build_state='VERIFIED'))
    monkeypatch.setenv('HOPE_DATABASE_URL','private-test-only')
    monkeypatch.setenv('NOTIFY_SOCKET','/private/socket')
    from hope.api import paper_service
    worker=Mock();monkeypatch.setattr(paper_service,'main',worker)
    stage=Mock(return_value=0);monkeypatch.setattr(s,'main',stage)
    execute=Mock(return_value=0)
    args=['--runtime-mode','release','--stage-only','--factory',FACTORY,'--stage-request',REQUEST,'--stage-timeout','300']
    if phase=='pinned':args+=['--approved-runtime-manifest','b'*64];monkeypatch.setattr(bootstrap,'verify_interpreter',Mock())
    assert bootstrap.main(args,release=release,execute=execute)==0
    worker.assert_not_called()
    if phase=='os':
        binary,forwarded,env=execute.call_args.args
        assert 'HOPE_DATABASE_URL' not in env and 'NOTIFY_SOCKET' not in env
        assert forwarded[6:]==s.arguments(release.name,'a'*64,FACTORY,REQUEST,300)
        stage.assert_not_called()
    else:stage.assert_called_once();execute.assert_not_called()


@pytest.mark.parametrize('extra',[['--runtime-mode','shared'],['--runtime-mode','release','--preflight']])
def test_stage_and_running_preflight_cannot_be_mixed(launch,extra):
    release,_=launch;execute=Mock()
    with pytest.raises(SystemExit):bootstrap.main(['--stage-only',*extra],release=release,execute=execute)
    execute.assert_not_called()


@pytest.mark.parametrize('field',['state','provenance_state','build_state'])
def test_stage_cannot_exec_with_unverified_package_or_build_authority(launch,field):
    release,authenticate=launch;binary,evidence=authenticate.return_value
    proof=dict(evidence,state='VERIFIED',provenance_state='VERIFIED',build_state='VERIFIED');proof[field]='UNVERIFIED'
    authenticate.return_value=(binary,proof);execute=Mock()
    with pytest.raises(SystemExit):bootstrap.main(['--stage-only','--runtime-mode','release'],release=release,execute=execute)
    execute.assert_not_called()


@pytest.fixture
def leaf(tmp_path,monkeypatch):
    """Model namespace inodes/argv; native lease/process tests are separate."""
    from hope.api import operations_deployment as ops
    from hope.infrastructure.paper_history import digest
    root=tmp_path/'operations';path=root/'staging/paper-stage.json';path.parent.mkdir(parents=True)
    proc=tmp_path/'proc';pid=1234;(proc/str(pid)).mkdir(parents=True)
    owner=(proc/str(pid)).stat().st_uid
    incarnation=('a'*8+'-aaaa-aaaa-aaaa-'+'a'*12,'123')
    process_hash=digest((pid,incarnation))
    monkeypatch.setattr(ops,'_process_identity',Mock(return_value=incarnation))
    now=datetime.now(timezone.utc)
    raw=dict(schema='hope.paper-stage.v1',state='WAITING',release_commit=SHA,factory=FACTORY,request=REQUEST,
             timeout=300,generated_at=now.isoformat(),execution_authorized=False,
             publisher_instance='c'*32,publisher_identity_sha256=process_hash)
    path.write_text(json.dumps(raw));path.chmod(0o640)
    lock=Path(str(path)+'.history.lock');lock.write_bytes(b'fixture-only');lock.chmod(0o640)
    namespace_fault=[False]
    NativePath=type(Path())
    class MappedPath(NativePath):
        def lstat(self):
            value=str(self);prefix=str(proc/str(pid)/'root/opt/hope-operations')
            target=Path(root/Path(value).relative_to(prefix)) if value.startswith(prefix) else Path(self)
            info=target.lstat()
            if namespace_fault[0] and value.startswith(prefix):
                return SimpleNamespace(st_dev=info.st_dev,st_ino=info.st_ino+1,st_uid=owner)
            if target==path.parent:
                return SimpleNamespace(st_dev=info.st_dev,st_ino=info.st_ino,st_uid=owner,st_mode=stat.S_IFDIR|0o750)
            return info
    def mapped(value):return MappedPath(proc) if str(value)=='/proc' else MappedPath(value)
    monkeypatch.setattr(s,'Path',mapped)
    if os.name!='posix':monkeypatch.setattr(s,'bounded_regular_read',lambda path,limit:path.read_bytes())
    publisher=Mock(return_value=dict(state='VERIFIED',identity_sha256=process_hash))
    monkeypatch.setattr(s,'publisher_observation',publisher)
    argv=[f'/opt/hope-operations/release-venvs/{SHA}/bin/python','-I','-B','-X',s.CACHE_PREFIX,
          f'/opt/hope-operations/releases/{SHA}/deploy/operations/paper_release.py']
    argv+=s.arguments(SHA,'b'*64,FACTORY,REQUEST,300)
    cmd=proc/str(pid)/'cmdline';cmd.write_bytes(b'\0'.join(v.encode() for v in argv)+b'\0')
    receipt=dict(release_commit=SHA,runtime_manifest_sha256='b'*64,process_sha256=process_hash)
    return root,path,raw,cmd,receipt,publisher,namespace_fault,now


@pytest.mark.parametrize('fault',[None,'stale','future','naive','state','factory','execution','extra','nonce','timeout',
                                 'publisher','kernel','namespace','argv','manifest','receipt_process','closing_status'])
def test_exact_stage_identity_namespace_argv_and_fresh_lease_are_mandatory(leaf,monkeypatch,fault):
    root,path,raw,cmd,receipt,publisher,namespace_fault,now=leaf
    if fault=='stale':raw['generated_at']=(now-timedelta(seconds=6)).isoformat()
    elif fault=='future':raw['generated_at']=(now+timedelta(seconds=1)).isoformat()
    elif fault=='naive':raw['generated_at']=now.replace(tzinfo=None).isoformat()
    elif fault=='state':raw['state']='CURRENT'
    elif fault=='factory':raw['factory']='other:build'
    elif fault=='execution':raw['execution_authorized']=True
    elif fault=='extra':raw['private']='secret'
    elif fault=='nonce':raw['request']='bad'
    elif fault=='timeout':raw['timeout']=301
    elif fault=='publisher':publisher.return_value['state']='BLOCKED'
    elif fault=='kernel':
        from hope.api import operations_deployment as ops
        ops._process_identity.side_effect=[('a','123'),('b','456')]
    elif fault=='namespace':namespace_fault[0]=True
    elif fault=='argv':cmd.write_bytes(cmd.read_bytes().replace(b'--stage-only',b'--once'))
    elif fault=='manifest':receipt['runtime_manifest_sha256']='d'*64
    elif fault=='receipt_process':receipt['process_sha256']='d'*64
    elif fault=='closing_status':
        reads=[]
        def changing(path,limit):
            reads.append(True)
            return json.dumps(dict(raw,state='STOPPED') if len(reads)>1 else raw).encode()
        monkeypatch.setattr(s,'bounded_regular_read',changing)
    path.write_text(json.dumps(raw));path.chmod(0o640)
    call=lambda:s.staged_process_observation(root,{'pid':1234},receipt,{'factory_module':'approved.factory','factory_name':'build'},now=now)
    if fault is None:
        result=call();assert result['expires_at']==int(now.timestamp())+5
        assert result['binding'][0]==receipt['process_sha256']
    else:
        with pytest.raises((RuntimeError,ValueError,TypeError,OSError)):call()


def test_legitimate_atomic_heartbeats_preserve_stage_binding_while_namespace_is_rechecked(leaf):
    root,path,raw,cmd,receipt,publisher,namespace_fault,now=leaf
    provider={'factory_module':'approved.factory','factory_name':'build'}
    first=s.staged_process_observation(root,{'pid':1234},receipt,provider,now=now)
    inode=path.stat().st_ino
    replacement=path.with_suffix('.next');replacement.write_text(json.dumps(dict(raw,generated_at=(now+timedelta(seconds=1)).isoformat())))
    replacement.chmod(0o640);replacement.replace(path)
    assert path.stat().st_ino!=inode
    second=s.staged_process_observation(root,{'pid':1234},receipt,provider,now=now+timedelta(seconds=1))
    assert second['binding']==first['binding'] and second['expires_at']==first['expires_at']+1
    namespace_fault[0]=True
    with pytest.raises(RuntimeError):s.staged_process_observation(root,{'pid':1234},receipt,provider,now=now+timedelta(seconds=1))


@NATIVE
def test_waiting_loop_never_imports_factory_connects_database_or_accepts_receipts(tmp_path,monkeypatch):
    (tmp_path/'staging').mkdir()
    monkeypatch.setenv('HOPE_LIVE_ENABLED','false');monkeypatch.setenv('HOPE_DEPLOYED_COMMIT',SHA)
    import sqlalchemy
    connect=Mock(side_effect=AssertionError('DB accessed'));monkeypatch.setattr(sqlalchemy,'create_engine',connect)
    ticks=iter([0,0,0,2]);stop=SimpleNamespace(is_set=lambda:False,wait=lambda t:None)
    assert s.run_stage(tmp_path,SHA,'never_import_stage_factory:build',REQUEST,timeout=1,stop=stop,clock=lambda:next(ticks))==0
    value=json.loads(s.status_path(tmp_path).read_bytes())
    assert value['state']=='STOPPED' and value['execution_authorized'] is False
    assert 'never_import_stage_factory' not in sys.modules
    assert publisher_observation(s.status_path(tmp_path))['state']=='BLOCKED'
    connect.assert_not_called()


@NATIVE
@pytest.mark.parametrize('signal_name',['terminate','kill'])
def test_real_stage_process_ownership_crash_and_restart_cannot_reuse_old_kernel_receipt(tmp_path,monkeypatch,signal_name):
    (tmp_path/'staging').mkdir();path=s.status_path(tmp_path)
    env=dict(os.environ,HOPE_LIVE_ENABLED='false',HOPE_DEPLOYED_COMMIT=SHA)
    script='''
import sys
from hope.api.paper_stage import main
raise SystemExit(main(['--factory','never_import_stage_factory:build','--stage-request','a'*64,'--stage-timeout','30'],release=sys.argv[1]))
'''
    release=tmp_path/'releases'/SHA
    def start():return subprocess.Popen([sys.executable,'-c',script,str(release)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    def waiting(child):
        import time
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if child.poll() is not None:pytest.fail('stage child exited: '+child.stderr.read().decode())
            try:
                raw=json.loads(path.read_bytes())
                if raw['state']=='WAITING' and publisher_observation(path)['state']=='VERIFIED':return raw
            except (OSError,ValueError):pass
            time.sleep(.02)
        pytest.fail('stage child did not publish owned WAITING')
    child=start();successor=None
    try:
        first=waiting(child);before=path.read_bytes()
        competing=start();competing.communicate(timeout=10)
        assert competing.returncode==2 and json.loads(path.read_bytes())['publisher_instance']==first['publisher_instance']
        getattr(child,signal_name)();child.communicate(timeout=10)
        assert publisher_observation(path)['state']=='BLOCKED'
        if signal_name=='terminate':assert json.loads(path.read_bytes())['state']=='STOPPED'
        successor=start();second=waiting(successor)
        assert first['publisher_identity_sha256']!=second['publisher_identity_sha256']
        assert first['publisher_instance']!=second['publisher_instance']
    finally:
        for process in (child,successor):
            if process is not None:
                if process.poll() is None:process.kill()
                process.communicate(timeout=10)


@NATIVE
def test_controller_stage_inspection_never_changes_journal_or_promotes_running_gate(tmp_path,monkeypatch,capsys):
    directory=tmp_path/'controller';c.initialize(directory,SHA,'b'*64,FACTORY)
    before=(directory/'state.json').read_bytes()
    report=dict(schema='hope.staged-deployment-observation.v1',state='STAGED',execution_authorized=False)
    inspect=Mock(return_value=report);monkeypatch.setattr(d,'inspect_staged_deployment',inspect)
    assert c.main(['inspect-stage','--directory',str(directory),'--root',str(tmp_path)])==0
    assert json.loads(capsys.readouterr().out)==report
    inspect.assert_called_once_with(tmp_path,SHA,expected_receipt='b'*64,expected_factory=FACTORY)
    assert (directory/'state.json').read_bytes()==before
    assert c.observation(directory,tmp_path)['state']=='STAGED'
    with c.controller(directory,tmp_path,inspector=Mock(return_value=report)) as owner:
        assert owner.verify(REQUEST)['events'][-1]['state']=='BLOCKED'
        owner.halt(REQUEST)
    with pytest.raises(RuntimeError):c._proof(report,c.plan(SHA,'b'*64,FACTORY),0)
    assert c.main(['inspect-stage','--directory',str(directory),'--root',str(tmp_path)])==1
    inspect.assert_called_once()


@NATIVE
def test_stage_deadline_stops_without_factory_and_live_environment_refuses_publication(tmp_path,monkeypatch):
    (tmp_path/'staging').mkdir();monkeypatch.setenv('HOPE_LIVE_ENABLED','true');monkeypatch.setenv('HOPE_DEPLOYED_COMMIT',SHA)
    with pytest.raises(RuntimeError):s.run_stage(tmp_path,SHA,FACTORY,REQUEST,timeout=1)
    assert not s.status_path(tmp_path).exists()
    monkeypatch.setenv('HOPE_LIVE_ENABLED','false')
    import time
    before=time.monotonic()
    assert s.run_stage(tmp_path,SHA,FACTORY,REQUEST,timeout=1)==0
    assert time.monotonic()-before<5
    assert json.loads(s.status_path(tmp_path).read_bytes())['state']=='STOPPED'
