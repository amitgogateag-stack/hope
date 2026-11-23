"""Real disposable file transactions. Service identity is explicitly modeled.

These are NOT host/runtime/signing or independent financial acceptance proofs.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

PATH = Path(__file__).resolve().parents[2] / 'deploy/operations/private_market_preview_install.py'
spec = importlib.util.spec_from_file_location('preview_files_under_test', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)



@pytest.fixture
def environment(tmp_path):
    if os.name != "posix":pytest.skip("Native POSIX ownership/fsync/atomic files")
    root, backups = tmp_path/'web', tmp_path/'backups'
    root.mkdir(mode=0o700);backups.mkdir(mode=0o700)
    (root/'index.html').write_bytes(b'<html>Original database dashboard</html>')
    (root/'snapshot.json').write_bytes(b'{"original":"genuine snapshot is not modified by tests"}')
    identity = {'sha256': 'c'*64, 'reader_uid': 99999, 'reader_gid': os.getegid() or 1}
    # Root test runners can use gid1; ordinary runners keep their actual group.
    layout = module.PreviewFiles(root, backups, os.geteuid(), lambda: dict(identity))
    observation = {'schema':'hope.market-observation.v1','scope':'PRIVATE_OBSERVATION_ONLY',
        'execution_authorized':False,'live_enabled':False,'independent_verified':False,'pit_certified':False,
        'scanner':{'state':'NOT_EVALUATED','reason':'NO_QUALIFIED_STRATEGY_BINDING','signals':None},
        'paper':{'state':'NOT_EXECUTED','positions':None,'cash':None,'pnl':None}}
    payloads = {'market-observations.json':json.dumps(observation).encode(),
                'market.html':b'<html>Private market view</html>', 'index.html':b'<html>Database plus market link</html>'}
    plan = {'schema':'hope.private-preview-file-plan.v1','source_commit':'a'*40,
        'before':{name:layout.current(name) for name in module.ORDER},
        'snapshot_sha256':module.digest((root/'snapshot.json').read_bytes()),
        'after':{name:module.digest(b) for name,b in payloads.items()},
        'reader_gid':identity['reader_gid'],'service_identity_sha256':identity['sha256'],
        'scope':'PRIVATE_STATIC_PREVIEW_ONLY','execution_authorized':False}
    return layout,plan,payloads,identity


def test_apply_and_rollback_preserve_original_snapshot_and_file_identity(environment):
    layout, plan, payloads, _ = environment
    original_snapshot = (layout.root/'snapshot.json').read_bytes()
    original_index = (layout.root/'index.html').read_bytes()
    backup = layout.apply(plan, payloads)
    assert json.loads((backup/'JOURNAL.json').read_bytes())['phase'] == 'COMMITTED'
    for name,b in payloads.items():
        assert (layout.root/name).read_bytes() == b
        assert layout.current(name) == layout.installed(plan,name)
    assert (layout.root/'snapshot.json').read_bytes() == original_snapshot
    layout.rollback(backup,plan)
    assert (layout.root/'index.html').read_bytes() == original_index
    assert (layout.root/'snapshot.json').read_bytes() == original_snapshot
    assert not (layout.root/'market.html').exists() and not (layout.root/'market-observations.json').exists()
    assert layout.current('index.html') == plan['before']['index.html']


@pytest.mark.parametrize('crash_after', module.ORDER)
def test_interrupted_install_can_roll_back_exact_known_partial_files(environment, crash_after):
    layout, plan, payloads, _ = environment
    def crash(name):
        if name == crash_after:raise RuntimeError('disposable interruption')
    with pytest.raises(RuntimeError,match='disposable interruption'):
        layout.apply(plan,payloads,crash)
    backup = layout.backups/('market-'+plan['source_commit'])
    assert json.loads((backup/'JOURNAL.json').read_bytes())['phase'] == 'PREPARED'
    layout.rollback(backup,plan)
    assert all(layout.current(n)==plan['before'][n] for n in module.ORDER)


def test_interrupted_rollback_can_resume_without_reapplying_candidate(environment):
    layout, plan, payloads, _ = environment
    backup = layout.apply(plan,payloads)
    with pytest.raises(RuntimeError):layout.rollback(backup,plan,lambda _: (_ for _ in ()).throw(RuntimeError('stop')))
    assert json.loads((backup/'JOURNAL.json').read_bytes())['phase']=='ROLLING_BACK'
    layout.rollback(backup,plan)
    assert all(layout.current(n)==plan['before'][n] for n in module.ORDER)


@pytest.mark.parametrize('fault',['snapshot','service','reader','asset','before','authority','unknown_file'])
def test_bad_preflight_never_creates_a_backup_or_changes_existing_files(environment,fault):
    layout,plan,payloads,identity=environment
    original=(layout.root/'index.html').read_bytes()
    if fault=='snapshot':plan['snapshot_sha256']='b'*64
    elif fault=='service':identity['sha256']='d'*64
    elif fault=='reader':identity['reader_gid']+=1
    elif fault=='asset':payloads['market.html']+=b'changed'
    elif fault=='before':plan['before']['index.html']=None
    elif fault=='authority':plan['execution_authorized']=True
    else:payloads['unexpected.json']=b'{}'
    with pytest.raises(ValueError):layout.apply(plan,payloads)
    assert (layout.root/'index.html').read_bytes()==original
    assert list(layout.backups.iterdir())==[]


@pytest.mark.parametrize('fault',['bytes','permissions','symlink','snapshot','backup','replay'])
def test_rollback_refuses_external_changes_without_overwriting_them(environment,fault):
    layout,plan,payloads,_=environment
    backup=layout.apply(plan,payloads)
    target=layout.root/'market.html'
    if fault=='bytes':target.write_bytes(b'operator changed this')
    elif fault=='permissions':target.chmod(0o600)
    elif fault=='symlink':target.unlink();target.symlink_to(layout.root/'index.html')
    elif fault=='snapshot':(layout.root/'snapshot.json').write_bytes(b'{"new":"generation"}')
    elif fault=='backup':(backup/'index.html').write_bytes(b'corrupt backup')
    else:layout.rollback(backup,plan)
    before={p.name:p.read_bytes() for p in layout.root.iterdir() if p.is_file()}
    with pytest.raises(ValueError):layout.rollback(backup,plan)
    assert {p.name:p.read_bytes() for p in layout.root.iterdir() if p.is_file()}==before


def test_replayed_install_never_overwrites_the_existing_backup(environment):
    layout,plan,payloads,_=environment
    backup=layout.apply(plan,payloads)
    protected=(backup/'index.html').read_bytes()
    with pytest.raises(ValueError):layout.apply(plan,payloads)
    assert (backup/'index.html').read_bytes()==protected


def test_rollback_cannot_use_a_changed_journal_as_a_new_operator_approval(environment):
    layout,plan,payloads,_=environment
    backup=layout.apply(plan,payloads)
    record=json.loads((backup/'JOURNAL.json').read_bytes())
    record['plan']['reader_gid']+=1
    (backup/'JOURNAL.json').write_text(json.dumps(record))
    before=(layout.root/'index.html').read_bytes()
    with pytest.raises(ValueError,match='ROLLBACK_APPROVAL_MISMATCH'):
        layout.rollback(backup,plan)
    assert (layout.root/'index.html').read_bytes()==before


@pytest.mark.parametrize('raw',[b'{"schema":"x","schema":"hope.market-observation.v1"}',b'{"x":NaN}',b'[]'])
def test_malformed_or_ambiguous_observation_is_not_installable(raw):
    with pytest.raises(ValueError):module.observation_payload(raw)


@pytest.mark.parametrize('field',['execution_authorized','live_enabled','independent_verified','pit_certified'])
def test_even_digest_pinned_preview_observations_cannot_grant_authority(field):
    value={'schema':'hope.market-observation.v1','scope':'PRIVATE_OBSERVATION_ONLY',
        'execution_authorized':False,'live_enabled':False,'independent_verified':False,'pit_certified':False,
        'scanner':{'state':'NOT_EVALUATED','reason':'NO_QUALIFIED_STRATEGY_BINDING','signals':None},
        'paper':{'state':'NOT_EXECUTED','positions':None,'cash':None,'pnl':None}}
    value[field]=True
    with pytest.raises(ValueError,match='PAYLOAD_SCOPE_INVALID'):
        module.observation_payload(json.dumps(value).encode())


def test_closing_snapshot_drift_retains_prepared_backup_and_refuses_false_success(environment):
    layout,plan,payloads,_=environment
    def change(name):
        if name=='index.html':(layout.root/'snapshot.json').write_bytes(b'{"external":"new snapshot"}')
    with pytest.raises(ValueError,match='SNAPSHOT_CHANGED'):layout.apply(plan,payloads,change)
    backup=layout.backups/('market-'+plan['source_commit'])
    assert json.loads((backup/'JOURNAL.json').read_bytes())['phase']=='PREPARED'
    assert (backup/'index.html').read_bytes()==b'<html>Original database dashboard</html>'
    with pytest.raises(ValueError,match='SNAPSHOT_CHANGED'):layout.rollback(backup,plan)
    assert (layout.root/'snapshot.json').read_bytes()==b'{"external":"new snapshot"}'


@pytest.fixture
def kernel_preview(environment,monkeypatch,unused_tcp_port):
    import subprocess,time,urllib.request
    layout,plan,payloads,identity=environment
    monkeypatch.setattr(module,'ROOT',layout.root)
    monkeypatch.setattr(module,'PORT',unused_tcp_port)
    child=subprocess.Popen(['/usr/bin/python3','-m','http.server',str(unused_tcp_port),
        '--bind','127.0.0.1','--directory',str(layout.root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+10
        while True:
            try:
                with urllib.request.urlopen('http://127.0.0.1:'+str(unused_tcp_port)+'/',timeout=1) as r:
                    assert r.status==200
                break
            except OSError:
                assert child.poll() is None
                if time.monotonic()>=deadline:raise
                time.sleep(0.05)
        values={'ActiveState':'active','SubState':'running','MainPID':str(child.pid),
            'User':'hope-dashboard-preview-20261122','Group':'','DynamicUser':'yes',
            'ExecStart':'{ argv[]=/usr/bin/python3 -m http.server '+str(unused_tcp_port)+' --bind 127.0.0.1 --directory '+str(layout.root)+' ; }',
            'ExecMainStartTimestampMonotonic':'MODELED_EXTERNAL_UNIT_ONLY'}
        def unit(*args,**kwargs):
            assert args[0][0:3]==['/usr/bin/systemctl','show','hope-dashboard-preview-20261122.service']
            return subprocess.CompletedProcess(args[0],0,stdout=''.join(k+'='+v+'\n' for k,v in values.items()).encode())
        monkeypatch.setattr(module.subprocess,'run',unit)
        yield layout,plan,payloads,values,unused_tcp_port
    finally:
        child.terminate();child.wait(timeout=5)


def test_actual_kernel_credentials_and_http_files_with_modeled_unit_identity(kernel_preview):
    import urllib.request
    layout,plan,payloads,values,port=kernel_preview
    observed=module.service_identity()
    assert observed['reader_uid']==os.getuid()>0
    assert observed['reader_gid']==os.getgid()>0
    plan['service_identity_sha256']=observed['sha256']
    plan['reader_gid']=observed['reader_gid']
    layout.observe_service=module.service_identity
    backup=layout.apply(plan,payloads)
    for name,b in payloads.items():
        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/'+name,timeout=2) as r:
            assert r.read()==b
    layout.rollback(backup,plan)
    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/',timeout=2) as r:
        assert r.read()==b'<html>Original database dashboard</html>'
    # A new start identity, even with an unchanged PID field, invalidates the pin.
    values['ExecMainStartTimestampMonotonic']='DIFFERENT_EXTERNAL_START'
    with pytest.raises(ValueError,match='SERVICE_IDENTITY_CHANGED'):layout.check(plan)


def test_a_configured_service_label_cannot_borrow_an_unrelated_process(kernel_preview,monkeypatch):
    layout,_,_,values,_=kernel_preview
    different=layout.root/'different-root'
    monkeypatch.setattr(module,'ROOT',different)
    values['ExecStart']=values['ExecStart'].replace(str(layout.root),str(different))
    with pytest.raises(ValueError,match='SERVICE_PROCESS_CHANGED'):module.service_identity()
