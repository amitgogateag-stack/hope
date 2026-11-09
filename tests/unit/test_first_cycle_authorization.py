"""Synthetic handoff models; native signatures and PG audit are separate tests."""
from contextlib import nullcontext
from copy import deepcopy
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from hope.api import first_cycle_authorization as a
from hope.api import runtime_integrity as r
from first_cycle_fixture import signed_case,model,SHA,REQUEST,RECEIPT,FACTORY


@pytest.fixture
def authorization_case(tmp_path,monkeypatch):
    now=int(time.time());token,stage,controller,policy=model(now)
    folder=tmp_path/'first-cycle-authorizations';folder.mkdir();ref=folder/f'{REQUEST}.ref'
    def pin():
        payload=r._canonical(token);sha=r._digest(payload);ref.write_text(sha,encoding='ascii')
        (folder/f'{sha}.json').write_bytes(payload)
        return sha
    pin()
    monkeypatch.setattr(a,'_activation_guard',lambda *args,**kwargs:nullcontext())
    monkeypatch.setattr(a.r,'_trusted',lambda path,**kw:r._identity(Path(path).stat()))
    monkeypatch.setattr(a.r,'_read',lambda path,limit,**kw:Path(path).read_bytes())
    monkeypatch.setattr(a.d,'signed',lambda path,key:(deepcopy(token),r._digest(path.read_bytes())))
    monkeypatch.setattr(a.d,'_key_digest',lambda path:a.digest(Path(path).name))
    monkeypatch.setattr(a.d,'_policy',Mock(side_effect=lambda *args,**kw:deepcopy(policy)))
    probes=dict(stage_probe=Mock(side_effect=lambda *args,**kw:deepcopy(stage)),
        runtime_probe=Mock(side_effect=lambda *args,**kw:deepcopy(controller)),
        process_probe=Mock(side_effect=lambda:dict(process_sha256=token['controller_process_sha256'],argv_sha256=token['controller_argv_sha256'])),
        clock=lambda:now)
    return tmp_path,token,stage,controller,policy,pin,probes,now


@pytest.mark.parametrize('fault',[None,'scope','live','paper','cycles','bool_cycles','future','expired','long_life',
    'receipt','request','factory','same_process','controller','controller_argv','controller_runtime','stage_expired',
    'stage_process','stage_runtime','stage_image','stage_ready','stage_authority','stage_provider','stage_extra',
    'policy_old','revoked','signature','selector','reference_race','stage_race','runtime_race','policy_race'])
def test_every_independent_authority_identity_and_closing_gate_is_required(authorization_case,monkeypatch,fault):
    root,token,stage,controller,policy,pin,probes,now=authorization_case
    if fault=='scope':token['scope']='START_LIVE'
    elif fault=='live':token['live_enabled']=True
    elif fault=='paper':token['paper_only']=False
    elif fault=='cycles':token['max_cycles']=2
    elif fault=='bool_cycles':token['max_cycles']=True
    elif fault=='future':token['issued_at']=now+1
    elif fault=='expired':token['expires_at']=now
    elif fault=='long_life':token['expires_at']=now+61
    elif fault=='receipt':token['deployment_receipt_sha256']='f'*64
    elif fault=='request':token['request_sha256']='f'*64
    elif fault=='factory':token['factory_sha256']='f'*64
    elif fault=='same_process':token['stage_process_sha256']=token['controller_process_sha256'];stage['process_sha256']=token['stage_process_sha256']
    elif fault=='controller':probes['process_probe'].side_effect=lambda:dict(process_sha256='0'*64,argv_sha256='f'*64)
    elif fault=='controller_argv':probes['process_probe'].side_effect=lambda:dict(process_sha256='e'*64,argv_sha256='0'*64)
    elif fault=='controller_runtime':controller['manifest_sha256']='0'*64
    elif fault=='stage_expired':stage['expires_at']=now
    elif fault in {'stage_process','stage_runtime','stage_image'}:
        stage[{'stage_process':'process_sha256','stage_runtime':'runtime_manifest_sha256','stage_image':'image_sha256'}[fault]]='0'*64
    elif fault=='stage_ready':stage['state']='VERIFIED'
    elif fault=='stage_authority':stage['execution_authorized']=True
    elif fault=='stage_provider':stage['provider']='UNKNOWN'
    elif fault=='stage_extra':stage['private']='secret'
    elif fault=='policy_old':policy['generation']=6
    elif fault=='revoked':policy['revoked_keys']=[a.digest(a.ROLE)]
    elif fault=='signature':monkeypatch.setattr(a.d,'signed',Mock(side_effect=RuntimeError('private signature')))
    elif fault=='reference_race':
        original=probes['runtime_probe'].side_effect
        def replaced(*args,**kw):
            path=root/'first-cycle-authorizations'/f'{REQUEST}.ref';new=path.with_suffix('.new');new.write_bytes(path.read_bytes());new.replace(path)
            return original(*args,**kw)
        probes['runtime_probe'].side_effect=replaced
    elif fault=='stage_race':probes['stage_probe'].side_effect=[deepcopy(stage),dict(stage,container_id='0'*64)]
    elif fault=='runtime_race':probes['runtime_probe'].side_effect=[deepcopy(controller),dict(controller,inventory_sha256='0'*64)]
    elif fault=='policy_race':a.d._policy.side_effect=[deepcopy(policy),dict(policy,generation=8)]
    selected=pin()
    expected='0'*64 if fault=='selector' else selected
    if fault is None:
        proof=a.verify(root,SHA,REQUEST,RECEIPT,FACTORY,expected_authorization=expected,**probes)
        assert proof.token['max_cycles']==1 and proof.expires_at==now+5
        proof.assert_current()
        with pytest.raises(TypeError):proof.token['max_cycles']=2
        report=a.observation(root,SHA,REQUEST,RECEIPT,FACTORY,**probes)
        assert report['state']=='AUTHORITY_VERIFIED' and report['execution_authorized'] is False
        assert FACTORY not in str(report) and str(root) not in str(report)
        probes['stage_probe'].assert_called_with(root,SHA,expected_receipt=RECEIPT,expected_factory=FACTORY)
    else:
        with pytest.raises((RuntimeError,OSError,ValueError)):a.verify(root,SHA,REQUEST,RECEIPT,FACTORY,expected_authorization=expected,**probes)


@pytest.mark.parametrize('role',['deployments-ed25519.pub','providers-ed25519.pub','data-acceptance-ed25519.pub',
    'rollback-ed25519.pub','artifacts-ed25519.pub','releases-ed25519.pub','builders-ed25519.pub','images-ed25519.pub','policy-ed25519.pub'])
def test_start_signer_is_independent_of_every_existing_role(authorization_case,monkeypatch,role):
    root,token,stage,controller,policy,pin,probes,now=authorization_case
    monkeypatch.setattr(a.d,'_key_digest',lambda path:a.digest(a.ROLE if Path(path).name==role else Path(path).name))
    with pytest.raises(RuntimeError):a.verify(root,SHA,REQUEST,RECEIPT,FACTORY,**probes)


def test_stale_or_replaced_authority_cannot_refresh_and_missing_evidence_never_probes_runtime(authorization_case):
    root,token,stage,controller,policy,pin,probes,now=authorization_case
    proof=a.verify(root,SHA,REQUEST,RECEIPT,FACTORY,**probes)
    token['first_cycle_key_sha256']='0'*64;pin()
    with pytest.raises(RuntimeError):proof.assert_current()
    probes['runtime_probe'].reset_mock()
    report=a.observation(root,SHA,'0'*64,RECEIPT,FACTORY,**probes)
    assert report['state']=='UNVERIFIED' and report['execution_authorized'] is False
    probes['runtime_probe'].assert_not_called()


@pytest.mark.parametrize('fault',['clock_reversal','closing_expiry','last_policy_revoked'])
def test_slow_final_observation_cannot_outlive_or_revoke_authority(authorization_case,fault):
    root,token,stage,controller,policy,pin,probes,now=authorization_case
    if fault=='last_policy_revoked':a.d._policy.side_effect=[deepcopy(policy),deepcopy(policy),dict(policy,revoked_keys=[a.digest(a.ROLE)])]
    else:
        ticks=iter([now,now,now,now,now,now+31 if fault=='closing_expiry' else now-1])
        probes['clock']=lambda:next(ticks)
    with pytest.raises(RuntimeError):a.verify(root,SHA,REQUEST,RECEIPT,FACTORY,**probes)


def test_controller_runtime_requires_independent_reviewed_runtime(tmp_path):
    with pytest.raises(RuntimeError):a.controller_runtime(tmp_path,SHA,'release','0'*64)


@pytest.mark.skipif(os.name!='posix',reason='Native kernel incarnation/argv probe')
def test_controller_kernel_identity_is_current_and_stable():
    first=a.process_observation();assert a.process_observation()==first
    assert all(a.is_hash(first[key]) for key in ('process_sha256','argv_sha256'))


@pytest.mark.parametrize('mode',['shared','release'])
@pytest.mark.parametrize('fault',[None,'stage_only','source_changed','build','interpreter','outside_entry','untracked_entry','entry_changed','argv'])
def test_controller_runtime_is_independently_bound_before_handoff(tmp_path,monkeypatch,mode,fault):
    root=tmp_path;release=root/'releases'/SHA
    source=release/'src/hope/api/first_cycle_authorization.py';source.parent.mkdir(parents=True);source.write_text('# model\n')
    monkeypatch.setattr(a,'__file__',str(source))
    monkeypatch.setattr(a,'sys',SimpleNamespace(flags=SimpleNamespace(isolated=True),dont_write_bytecode=True))
    python=r.environment_path(root,SHA,mode)/'bin/python'
    entry=release/'deploy/operations/controller.py';entry.parent.mkdir(parents=True);entry.write_bytes(b'# entry\n')
    if fault=='outside_entry':entry=tmp_path/'outside.py';entry.write_bytes(b'# entry\n')
    approved={'deploy/operations/controller.py':b'# entry\n'}
    monkeypatch.setattr(a,'_source',Mock(side_effect=[approved,{}] if fault=='entry_changed' else None,return_value={} if fault=='untracked_entry' else approved))
    monkeypatch.setattr(r,'_read',lambda path,*args,**kwargs:path.read_bytes())
    command=tmp_path/'cmdline';values=[str(python),'-I','-B','-X',a.CACHE_PREFIX,str(entry)]+(['--stage-only'] if fault=='stage_only' else [])
    if fault=='argv':values[1]='-E'
    command.write_bytes(b'\0'.join(os.fsencode(value) for value in values)+b'\0')
    monkeypatch.setattr(a,'Path',lambda value:command if str(value)=='/proc/self/cmdline' else Path(value))
    monkeypatch.setattr(a,'_process_identity',Mock(return_value=('boot','123')))
    monkeypatch.setattr(a,'inspect_release',Mock())
    monkeypatch.setattr(r,'source_snapshot',Mock(side_effect=[{'same':'source'},{'changed':'source'}] if fault=='source_changed' else None,return_value={'same':'source'}))
    _,_,proof,_=model(int(time.time()));proof.update(state='VERIFIED',provenance_state='VERIFIED',build_state='VERIFIED')
    if fault=='build':proof['build_state']='UNVERIFIED'
    monkeypatch.setattr(r,'attest_runtime',Mock(return_value=proof))
    interpreter=Mock(side_effect=RuntimeError('private') if fault=='interpreter' else None);monkeypatch.setattr(a,'verify_interpreter',interpreter)
    listener=Mock();monkeypatch.setattr(a,'verify_listener',listener)
    if fault is None:
        result=a.controller_runtime(root,SHA,mode,'1'*64)
        assert result['manifest_sha256']=='1'*64
        listener.assert_called_once_with(os.getpid(),python,identity=a._process_identity,managed=False,
                                         expected_identity=('boot','123'),expected_arguments=values[1:])
    else:
        with pytest.raises((RuntimeError,ValueError)):a.controller_runtime(root,SHA,mode,'1'*64)


@pytest.mark.parametrize('fault',[None,'signature','document','revoked','role_reuse','expired','replacement'])
def test_native_independent_signatures_and_current_kernel_are_required(tmp_path,monkeypatch,fault):
    case=signed_case(tmp_path,monkeypatch);root=case['root'];selected=case['pin']()
    path=root/'first-cycle-authorizations'/f'{selected}.json'
    if fault=='signature':path.with_suffix('.sig').write_bytes(b'x'*64)
    elif fault=='document':path.write_bytes(path.read_bytes()+b' ')
    elif fault=='revoked':case['policy']['revoked_keys']=[a.d._key_digest(root/'runtime-trust'/a.ROLE)];case['policy_pin']()
    elif fault=='role_reuse':
        (root/'runtime-trust/artifacts-ed25519.pub').write_bytes((root/'runtime-trust'/a.ROLE).read_bytes())
    elif fault=='expired':case['token']['expires_at']=int(time.time())-1;case['pin']()
    if fault is None or fault=='replacement':
        proof=case['verify']();proof.assert_current()
        if fault=='replacement':
            case['token']['first_cycle_key_sha256']='0'*64;case['pin']()
            with pytest.raises(RuntimeError):proof.assert_current()
        else:assert proof.token['controller_process_sha256']==a.process_observation()['process_sha256']
    else:
        with pytest.raises(RuntimeError):case['verify']()


@pytest.mark.skipif(os.name!='posix',reason='Native owner-scoped controller journal')
def test_controller_handoff_inspection_is_readonly_and_halt_remains_authoritative(tmp_path,monkeypatch):
    from hope.api import paper_controller as c
    case=signed_case(tmp_path,monkeypatch);directory=tmp_path/'controller'
    c.initialize(directory,SHA,RECEIPT,FACTORY)
    before=(directory/'state.json').read_bytes()
    with c.controller(directory,case['root']) as controller:
        report=controller.inspect_handoff(REQUEST,**case['probes'])
        assert report['state']=='AUTHORITY_VERIFIED' and report['execution_authorized'] is False
        assert (directory/'state.json').read_bytes()==before
        controller.halt(REQUEST)
        with pytest.raises(RuntimeError,match='HALT_LATCHED'):controller.inspect_handoff(REQUEST,**case['probes'])
