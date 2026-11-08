"""Contract state-machine fixtures are synthetic, never operational acceptance."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
import time

import pytest
from hope.api import deployment_contract as d,runtime_integrity as r
from test_release_builder import build_case,trusted_python

SHA='a'*40


@pytest.fixture
def contract(tmp_path,monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(d,'_activation_guard',lambda *a,**k:nullcontext())
    now=int(time.time());root=tmp_path
    receipt=dict(schema='hope.deployment-receipt.v1',scope='PRODUCTION_PAPER',release_commit=SHA,image_sha256='b'*64,
        runtime_manifest_sha256='c'*64,host_sha256='d'*64,boot_sha256='e'*64,container_id='f'*64,engine='docker',
        process_sha256='1'*64,argv_sha256='2'*64,python_sha256='3'*64,os_release_sha256='4'*64,postgres_major=17,
        minimum_systemd=249,provider_sha256='5'*64,rollback_sha256='6'*64,minimum_policy_generation=7,issued_at=now-1,expires_at=now+300)
    folder=root/'deployment-receipts';folder.mkdir();reference=folder/f'{SHA}.ref'
    def pin():
        payload=r._canonical(receipt);digest=r._digest(payload);reference.write_text(digest,encoding='ascii')
        (folder/f'{digest}.json').write_bytes(payload)
        return digest
    pin()
    # Model already authenticated inputs for the pure reconciliation matrix.
    # Real file ownership/signatures are exercised independently on native CI.
    monkeypatch.setattr(d.r,'_read',lambda path,limit,**kw:Path(path).read_bytes())
    monkeypatch.setattr(d.r,'_trusted',lambda path,**kw:(Path(path).stat().st_ino,Path(path).stat().st_mtime_ns))
    monkeypatch.setattr(d,'signed',lambda path,key:(deepcopy(receipt),r._digest(path.read_bytes())))
    image=dict(schema='hope.image-observation.v1',scope='PRODUCTION_PAPER',state='VERIFIED',release_commit=SHA,
        image_sha256='b'*64,expires_at=now+200,policy_generation=7,execution_authorized=False)
    monkeypatch.setattr(d,'verify_image',Mock(side_effect=lambda *a,**k:deepcopy(image)))
    monkeypatch.setattr(d,'_key_digest',lambda path:str(path))
    monkeypatch.setattr(d,'authorities',Mock(return_value={'synthetic-authority'}))
    provider=dict(dataset_sha256='7'*64,calendar_sha256='8'*64)
    monkeypatch.setattr(d,'provider_contract',Mock(return_value=provider))
    accepted=dict(expires_at=now+100)
    monkeypatch.setattr(d,'data_acceptance',Mock(return_value=accepted))
    monkeypatch.setattr(d,'rollback_contract',Mock(return_value={}))
    host=dict(host_sha256='d'*64,boot_sha256='e'*64,os_release_sha256='4'*64,python_version=[3,13],postgres_major=17,systemd_major=255)
    container=dict(container_id='f'*64,config_sha256='9'*64,pid=1234,network='hope-paper')
    layout=root/'image-evidence/oci/blobs/sha256';layout.mkdir(parents=True)
    (layout/('b'*64)).write_bytes(r._canonical(dict(config=dict(digest='sha256:'+'9'*64))))
    probes=dict(now=now,host_probe=Mock(side_effect=lambda:deepcopy(host)),container_probe=Mock(side_effect=lambda *a:deepcopy(container)),
        process_probe=Mock(return_value='1'*64),runtime_probe=Mock(return_value=dict(manifest_sha256='c'*64)),
        host_readiness=Mock(return_value=dict(ready=True,units={'hope-paper.service':{'MainPID':'1234'}},runtime_integrity=dict(manifest_sha256='c'*64))))
    return root,receipt,pin,image,host,container,probes


@pytest.mark.parametrize('fault',[None,'lab_image','unsigned_provider','stale_acceptance','rollback','host','boot','python','postgres','systemd',
    'container_config','process','runtime','host_blocked','receipt_expired','receipt_future','concurrent_host','reference_replaced','policy_changed'])
def test_every_independent_gate_and_closing_observation_is_required(contract,monkeypatch,fault):
    root,receipt,pin,image,host,container,probes=contract
    if fault=='lab_image':image['scope']='DISPOSABLE_LAB_ONLY'
    elif fault in {'unsigned_provider','stale_acceptance','rollback'}:
        name={'unsigned_provider':'provider_contract','stale_acceptance':'data_acceptance','rollback':'rollback_contract'}[fault]
        monkeypatch.setattr(d,name,Mock(side_effect=RuntimeError('private fixture secret')))
    elif fault in {'host','boot','python','postgres','systemd'}:
        name={'host':'host_sha256','boot':'boot_sha256','python':'python_version','postgres':'postgres_major','systemd':'systemd_major'}[fault]
        host[name]=[3,12] if fault=='python' else 15 if fault=='postgres' else 248 if fault=='systemd' else '0'*64
    elif fault=='container_config':container['config_sha256']='0'*64
    elif fault=='process':probes['process_probe'].return_value='0'*64
    elif fault=='runtime':probes['runtime_probe'].return_value['manifest_sha256']='0'*64
    elif fault=='host_blocked':probes['host_readiness'].return_value['ready']=False
    elif fault in {'receipt_expired','receipt_future'}:
        receipt['expires_at' if fault=='receipt_expired' else 'issued_at']=probes['now'] if fault=='receipt_expired' else probes['now']+1;pin()
    elif fault=='concurrent_host':
        probes['host_probe'].side_effect=[deepcopy(host),dict(host,boot_sha256='0'*64)]
    elif fault=='reference_replaced':
        def replaced(*args,**kwargs):
            reference=root/'deployment-receipts'/f'{SHA}.ref';other=reference.with_suffix('.new')
            other.write_bytes(reference.read_bytes());other.replace(reference)
            return dict(ready=True,units={'hope-paper.service':{'MainPID':'1234'}},runtime_integrity=dict(manifest_sha256='c'*64))
        probes['host_readiness'].side_effect=replaced
    elif fault=='policy_changed':
        monkeypatch.setattr(d,'verify_image',Mock(side_effect=[deepcopy(image),dict(image,policy_generation=8)]))
    result=d.inspect_deployment(root,SHA,**probes)
    assert result['state']==('VERIFIED' if fault is None else 'BLOCKED')
    assert result['execution_authorized'] is False
    assert 'secret' not in str(result) and str(root) not in str(result)
    if fault is None:assert result['expires_at']==probes['now']+100 and not result['blockers']
    else:assert result['blockers']


def test_absent_receipt_never_probes_host_or_echoes_private_paths(tmp_path,monkeypatch):
    from contextlib import nullcontext
    # Isolate missing authority from native ACL/flock preconditions.
    monkeypatch.setattr(d,'_activation_guard',lambda *a,**k:nullcontext())
    monkeypatch.setattr(r,'_trusted',lambda path,**kw:(Path(path).stat().st_ino,))
    probe=Mock(side_effect=AssertionError('must not probe before authority'))
    result=d.inspect_deployment(tmp_path,SHA,host_probe=probe)
    assert result['state']=='UNKNOWN' and result['receipt_sha256'] is None
    probe.assert_not_called()


@pytest.mark.parametrize('text',['','systemd invalid','private diagnostic output'])
def test_unverifiable_systemd_is_not_compatible(text):
    with pytest.raises(RuntimeError):d.host_observation(capture=lambda *a,**k:text)


def test_readonly_host_probe_never_changes_database_transaction_mode(monkeypatch):
    monkeypatch.setattr(d.r,'_read',lambda *a,**k:b'public fixture metadata')
    monkeypatch.setattr(d.Path,'resolve',lambda self,**k:self)
    from hope.api import operations_deployment
    monkeypatch.setattr(operations_deployment,'_process_identity',lambda _:('fixture-boot','1'))
    connection=Mock();connection.execute.return_value.scalar_one.side_effect=['off']
    with pytest.raises(RuntimeError):d.host_observation(connection=connection,capture=lambda *a,**k:'systemd 255\n')
    assert connection.execute.call_count==1
    connection.execute.return_value.scalar_one.side_effect=['on','170008']
    observed=d.host_observation(connection=connection,capture=lambda *a,**k:'systemd 255\n')
    assert observed['postgres_major']==17 and observed['python_version']==[3,13]


@pytest.mark.parametrize('fault',[None,'environment','pid','privileged','writable','host_network','image','root_user'])
def test_container_probe_is_allowlisted_bounded_and_never_reads_environment(monkeypatch,fault):
    monkeypatch.setattr(d.r,'_trusted',lambda *a,**k:None)
    monkeypatch.setattr(d.Path,'resolve',lambda self,**k:self)
    values=['f'*64,'sha256:'+'9'*64,True,123,False,True,'hope-paper','65532:65532']
    if fault=='pid':values[3]=True
    elif fault=='privileged':values[4]=True
    elif fault=='writable':values[5]=False
    elif fault=='host_network':values[6]='host'
    elif fault=='image':values[1]='mutable:tag'
    elif fault=='root_user':values[7]='0:0'
    elif fault=='environment':values.append({'secret':'must not be read'})
    capture=Mock(return_value='\n'.join(r._canonical(v).decode() for v in values))
    if fault is None:
        assert d.container_observation('docker','f'*64,capture=capture)['pid']==123
    else:
        with pytest.raises(RuntimeError):d.container_observation('docker','f'*64,capture=capture)
    args=capture.call_args.args[0]
    assert Path(args[0])==Path('/usr/bin/docker') and 'Env' not in args[5] and 'Mounts' not in args[5]
    assert capture.call_args.kwargs['max_output']==4096


def test_real_independent_provider_and_acceptance_signatures_bind_current_source(build_case):
    import shutil,subprocess
    from test_release_image import authority,write_signed
    args,_,_,root=build_case;source=args['source']
    (source/'src/fixture.py').write_text('def factory(connection,current):\n    raise RuntimeError("Synthetic fixture only")\n')
    subprocess.run(['/usr/bin/git','-C',str(source),'add','.'],check=True,capture_output=True)
    subprocess.run(['/usr/bin/git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','test factory'],check=True,capture_output=True)
    sha=subprocess.check_output(['/usr/bin/git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    release=root/'releases'/sha;release.parent.mkdir();shutil.copytree(source,release)
    trust=root/'runtime-trust';trust.mkdir();folder=root/'paper-provider';folder.mkdir()
    private,public=authority(args['inputs'].parent,'provider-test');shutil.copyfile(public,trust/'providers-ed25519.pub')
    data_private,data_public=authority(args['inputs'].parent,'data-test');shutil.copyfile(data_public,trust/'data-acceptance-ed25519.pub')
    now=int(time.time())
    provider=dict(schema='hope.paper-provider.v1',scope='APPROVED_REAL_DATA',release_commit=sha,factory_module='fixture',factory_name='factory',
        factory_sha256=r._digest((release/'src/fixture.py').read_bytes()),dataset_sha256='a'*64,calendar_sha256='b'*64,
        markets=['INDIA','USA'],issued_at=now-1,expires_at=now+600)
    path=folder/f'{sha}.json';write_signed(path,provider,private);digest=r._digest(path.read_bytes())
    assert d.provider_contract(root,sha,digest,now)==provider
    accepted=dict(schema='hope.real-data-acceptance.v1',scope='REAL_DATA',release_commit=sha,provider_sha256=digest,dataset_sha256='a'*64,
        calendar_sha256='b'*64,issued_at=now-1,expires_at=now+60,market_data='FRESH_COMPLETE_PIT',scheduler='CURRENT',risk='CLEAR',reconciliation='CLEAR',live_enabled=False)
    acceptance=folder/f'{sha}.acceptance.json';write_signed(acceptance,accepted,data_private)
    assert d.data_acceptance(root,sha,provider,digest,now)==accepted
    for key,value in [('scope','DISPOSABLE_LAB_ONLY'),('market_data','STALE'),('reconciliation','MISMATCH'),('risk','HALTED'),('live_enabled',True),('dataset_sha256','f'*64),('expires_at',now)]:
        bad=dict(accepted,**{key:value});write_signed(acceptance,bad,data_private)
        with pytest.raises(RuntimeError):d.data_acceptance(root,sha,provider,digest,now)
    write_signed(acceptance,accepted,private)
    with pytest.raises(RuntimeError):d.data_acceptance(root,sha,provider,digest,now)
    (release/'src/fixture.py').write_text('# changed after approval\n')
    with pytest.raises(Exception):d.provider_contract(root,sha,digest,now)

@pytest.mark.parametrize('mode',['shared','release'])
def test_independent_rollback_signature_archive_and_git_binding(build_case,monkeypatch,mode):
    import shutil
    from test_release_image import authority,write_signed
    args,plan,_,root=build_case;prior=plan['release_commit']
    release=root/'releases'/prior;release.parent.mkdir();shutil.copytree(args['source'],release)
    trust=root/'runtime-trust';trust.mkdir();receipts=root/'deployment-receipts';receipts.mkdir()
    private,public=authority(args['inputs'].parent,'rollback-test');shutil.copyfile(public,trust/'rollback-ed25519.pub')
    builds=root/'release-builds';builds.mkdir();archive=builds/f'{prior}-{mode}.tar';archive.write_bytes(b'explicit synthetic rollback binding fixture')
    now=int(time.time());value=dict(schema='hope.rollback-receipt.v1',release_commit=SHA,previous_release=prior,runtime_mode=mode,
        manifest_sha256='c'*64,archive_sha256=r._digest(archive.read_bytes()),issued_at=now-1,expires_at=now+300)
    digest=r._digest(r._canonical(value));path=receipts/f'{digest}.rollback.json';write_signed(path,value,private)
    proof=Mock(return_value=dict(state='VERIFIED',provenance_state='VERIFIED',build_state='VERIFIED'))
    monkeypatch.setattr(r,'attest_runtime',proof)
    assert d.rollback_contract(root,SHA,digest,now)==value
    proof.assert_called_once_with(root,prior,mode,expected_manifest='c'*64)
    archive.write_bytes(b'changed after independent approval')
    with pytest.raises(RuntimeError):d.rollback_contract(root,SHA,digest,now)
    (release/'src/fixture.py').write_text('# corrupt prior source\n')
    with pytest.raises(Exception):d.rollback_contract(root,SHA,digest,now)


@pytest.mark.parametrize('fault',[None,'revoked','role_reuse','image_role_reuse'])
def test_all_new_approval_roles_are_independent_and_revocable(tmp_path,monkeypatch,fault):
    keys={name:chr(97+index)*64 for index,name in enumerate((
        'deployments-ed25519.pub','providers-ed25519.pub','data-acceptance-ed25519.pub','rollback-ed25519.pub',
        'images-ed25519.pub','policy-ed25519.pub'))}
    if fault=='role_reuse':keys['providers-ed25519.pub']=keys['deployments-ed25519.pub']
    if fault=='image_role_reuse':keys['providers-ed25519.pub']=keys['images-ed25519.pub']
    monkeypatch.setattr(d,'_key_digest',lambda path:keys[Path(path).name])
    monkeypatch.setattr(d,'_policy',lambda *a,**k:dict(revoked_keys=[keys['rollback-ed25519.pub']] if fault=='revoked' else []))
    if fault is None:assert len(d.authorities(tmp_path,tmp_path,0))==4
    else:
        with pytest.raises(RuntimeError):d.authorities(tmp_path,tmp_path,0)

@pytest.mark.parametrize('fault',[None,'namespace_substitution','source_substitution','bootstrap_substitution','wrong_build','wrong_packages','concurrent_inventory'])
def test_running_namespace_must_be_same_approved_runtime_not_a_lookalike(tmp_path,monkeypatch,fault):
    from types import SimpleNamespace
    root=tmp_path;environment=root/'release-venvs'/SHA;release=root/'releases'/SHA
    def metadata(number):
        return SimpleNamespace(st_dev=1,st_ino=number,st_mode=0o100644,st_uid=0,st_gid=0,st_nlink=1,st_size=1,st_mtime_ns=1,st_ctime_ns=1)
    identities={name:metadata(index) for index,name in enumerate(('runtime','python','lib64','src','factory.py','paper.py'),1)}
    before=({'.':r._identity(identities['runtime'])},{'bin/python':r._identity(identities['python'])},
        {'lib64':('@runtime/lib',r._identity(identities['lib64']))},'interpreter',())
    inventory=(environment,environment/'bin/python',environment/'bin/python',before)
    if fault=='concurrent_inventory':
        monkeypatch.setattr(r,'_inventory',Mock(side_effect=[inventory,(*inventory[:3],({'changed':()}, {}, {},'',()))]))
    else:monkeypatch.setattr(r,'_inventory',Mock(return_value=inventory))
    source={str(release/'src'):r._identity(identities['src']),str(release/'src/factory.py'):r._identity(identities['factory.py'])}
    monkeypatch.setattr(r,'source_snapshot',Mock(return_value=source))
    from hope.api import release_builder
    monkeypatch.setattr(release_builder,'_source',Mock(return_value={'deploy/paper.py':b'approved source'}))
    monkeypatch.setattr(r,'_trusted',lambda path,**kw:r._identity(identities['runtime' if Path(path).name==SHA else Path(path).name]))
    proof=dict(state='VERIFIED',provenance_state='VERIFIED',build_state='VERIFIED',manifest_sha256='c'*64,
        build_sha256='d'*64,provenance_sha256='e'*64)
    if fault=='wrong_build':proof['build_sha256']='0'*64
    if fault=='wrong_packages':proof['provenance_sha256']='0'*64
    attest=Mock(return_value=proof);monkeypatch.setattr(r,'attest_runtime',attest)
    monkeypatch.setattr(r,'_read',lambda *a,**k:r._canonical(dict(release_approval_sha256='d'*64,hashes={'package-provenance.json':'e'*64})))
    def stat_node(path):
        name=Path(path).name;name='runtime' if name==SHA else name
        if (fault=='namespace_substitution' and name=='python') or (fault=='source_substitution' and name=='factory.py') or (fault=='bootstrap_substitution' and name=='paper.py'):return metadata(999)
        return identities[name]
    if fault is None:
        assert d.runtime_observation(root,SHA,{'pid':1234},{'runtime_manifest_sha256':'c'*64},node_stat=stat_node)==proof
        attest.assert_called_once_with(root,SHA,'release',expected_manifest='c'*64)
    else:
        with pytest.raises(RuntimeError):d.runtime_observation(root,SHA,{'pid':1234},{'runtime_manifest_sha256':'c'*64},node_stat=stat_node)

@pytest.mark.skipif(__import__('os').name!='posix',reason='Native read-only activation flock exercised on Linux CI')
def test_complete_contract_holds_existing_activation_fence_during_authority_reads(contract,monkeypatch):
    import subprocess,sys
    from hope.api.operations_deployment import _activation_guard
    root,_,_,_,_,_,probes=contract
    monkeypatch.setattr(d,'_activation_guard',_activation_guard)
    signed=d.signed;attempts=[]
    code='''
import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY)
try:
 try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);print('ACQUIRED')
 except BlockingIOError:print('BLOCKED')
finally:os.close(fd)
'''
    def observe_authority(path,key):
        result=subprocess.run([sys.executable,'-c',code,str(root)],capture_output=True,text=True,check=True,timeout=5)
        attempts.append(result.stdout.strip())
        return signed(path,key)
    monkeypatch.setattr(d,'signed',observe_authority)
    assert d.inspect_deployment(root,SHA,**probes)['state']=='VERIFIED'
    assert attempts and set(attempts)=={'BLOCKED'}
    result=subprocess.run([sys.executable,'-c',code,str(root)],capture_output=True,text=True,check=True,timeout=5)
    assert result.stdout.strip()=='ACQUIRED'
