from copy import deepcopy
from datetime import UTC,datetime
import os,time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from first_cycle_fixture import model,SHA,REQUEST
from startup_fixture import startup_case
from hope.api import startup_authority as a
from hope.application.jobs import create_scheduled_job_run
from hope.infrastructure.paper_service import logical_cycle_key

RUN=create_scheduled_job_run('paper:USA:startup-test',datetime(2026,11,8,tzinfo=UTC))


def modeled(kind):
    token,stage,controller,policy=model(int(time.time()));token['first_cycle_key_sha256']=logical_cycle_key(RUN)
    intent=SimpleNamespace(token=token,authorization_sha256='a'*64)
    base=intent if kind=='startup' else SimpleNamespace(base=intent,authorization_sha256='b'*64)
    values={key:token[key] for key in ('release_commit','controller_process_sha256','database_sha256','first_cycle_key_sha256','minimum_policy_generation','issued_at','expires_at','paper_only','live_enabled')}
    values.update(schema='hope.paper-'+kind+'-authority.v1',scope='BOOTSTRAP_ONLY' if kind=='startup' else 'ONE_CERTIFIED_PAPER_CYCLE',
                  request_sha256=REQUEST,handoff_authorization_sha256='a'*64,max_cycles=0 if kind=='startup' else 1)
    if kind=='startup':values.update(schema_catalog_sha256='c'*64,controller_namespace_sha256='d'*64)
    else:values.update(startup_authorization_sha256='b'*64,job_run_id=str(RUN.job_run_id),job_key=RUN.job_key,scheduled_for=RUN.scheduled_for.isoformat())
    return values,base


@pytest.mark.parametrize('kind',['startup','execution'])
@pytest.mark.parametrize('fault',[None,'extra','schema','scope','request','intent','release','controller','database','cycle','generation','generation_bool','expired','future','long','cycles','cycles_bool','live','paper'])
def test_separate_authority_schemas_never_promote_intent_or_accept_relaxed_bindings(kind,fault):
    values,base=modeled(kind);now=int(time.time())
    field={'schema':'schema','scope':'scope','request':'request_sha256','intent':'handoff_authorization_sha256','release':'release_commit',
           'controller':'controller_process_sha256','database':'database_sha256','cycle':'first_cycle_key_sha256'}.get(fault)
    if field:values[field]='wrong'
    elif fault=='extra':values['operational_ready']=True
    elif fault=='generation':values['minimum_policy_generation']=0
    elif fault=='generation_bool':values['minimum_policy_generation']=True
    elif fault=='expired':values['expires_at']=now-1
    elif fault=='future':values.update(issued_at=now+1,expires_at=now+2)
    elif fault=='long':values['expires_at']=now+61
    elif fault=='cycles':values['max_cycles']=1 if kind=='startup' else 0
    elif fault=='cycles_bool':values['max_cycles']=False if kind=='startup' else True
    elif fault=='live':values['live_enabled']=True
    elif fault=='paper':values['paper_only']=False
    if fault is None:a._validate(values,kind,base,REQUEST,now)
    else:
        with pytest.raises(RuntimeError):a._validate(values,kind,base,REQUEST,now)


@pytest.mark.parametrize('fault',['startup','id','key_padding','time_naive','time_noncanonical','key','catalog'])
def test_execution_schedule_and_bootstrap_catalog_are_exact(fault):
    kind='startup' if fault=='catalog' else 'execution';values,base=modeled(kind)
    if fault=='catalog':values['schema_catalog_sha256']='bad'
    elif fault=='startup':values['startup_authorization_sha256']='0'*64
    elif fault=='id':values['job_run_id']='00000000-0000-0000-0000-000000000000'
    elif fault=='key_padding':values['job_key']=' '+values['job_key']
    elif fault=='time_naive':values['scheduled_for']='2026-11-08T00:00:00'
    elif fault=='time_noncanonical':values['scheduled_for']='2026-11-08T00:00:00Z'
    else:values['job_key']='paper:other'
    with pytest.raises((RuntimeError,ValueError)):a._validate(values,kind,base,REQUEST,int(time.time()))


@pytest.mark.skipif(os.name!='posix',reason='Real POSIX/OpenSSL authority and kernel incarnation tests')
@pytest.mark.parametrize('fault',[None,'startup_signature','execution_signature','same_role','intent_role_reuse','revoked_startup','revoked_execution','policy_rollback','reference_replacement','process_replacement'])
def test_independent_native_keys_signatures_policy_and_closing_refresh(tmp_path,monkeypatch,fault):
    case=startup_case(tmp_path,monkeypatch,RUN);root=case['root']
    if fault in {'startup_signature','execution_signature'}:
        kind=fault.split('_')[0];value=case[kind];folder=root/(kind+'-authorizations');digest=(folder/(value['request_sha256']+'.ref')).read_text()
        (folder/(digest+'.sig')).write_bytes(b'forged')
    elif fault=='same_role':(root/'runtime-trust'/a.EXECUTION_ROLE).write_bytes((root/'runtime-trust'/a.STARTUP_ROLE).read_bytes())
    elif fault=='intent_role_reuse':(root/'runtime-trust'/a.STARTUP_ROLE).write_bytes((root/'runtime-trust'/a.h.ROLE).read_bytes())
    elif fault in {'revoked_startup','revoked_execution'}:
        key=a.STARTUP_ROLE if fault=='revoked_startup' else a.EXECUTION_ROLE
        case['policy']['revoked_keys']=[a.h.d._key_digest(root/'runtime-trust'/key)];case['policy_pin']()
    elif fault=='policy_rollback':case['policy']['generation']=6;case['policy_pin']()
    if fault in {None,'reference_replacement','process_replacement'}:
        startup=case['verify_startup']();execution=case['verify_execution'](startup)
        assert startup.token['max_cycles']==0 and execution.token['max_cycles']==1 and execution.run==RUN
        if fault=='reference_replacement':case['execution']['issued_at']-=1;case['pin_authority']('execution')
        elif fault=='process_replacement':case['token']['controller_process_sha256']='0'*64;case['pin']()
        if fault is None:execution.assert_current()
        else:
            with pytest.raises(RuntimeError):execution.assert_current()
    else:
        with pytest.raises((RuntimeError,FileNotFoundError)):case['verify_execution']()


def test_invalid_startup_signature_is_checked_before_any_intent_or_host_probe(tmp_path,monkeypatch):
    verifier=Mock();monkeypatch.setattr(a,'document',Mock(side_effect=FileNotFoundError()))
    with pytest.raises(FileNotFoundError):a.verify_startup(tmp_path,SHA,'a'*64,'b'*64,'c'*64,'approved:build','d'*64,intent_verifier=verifier)
    verifier.assert_not_called()


@pytest.mark.parametrize('state,code',[('HALTED','HALT_LATCHED'),('VERIFYING','VERIFYING'),('VERIFIED','EVIDENCE_VERIFIED'),('BLOCKED','RECOVERY_REQUIRED')])
def test_startup_never_adopts_halted_interrupted_or_running_controller_journals(state,code):
    from hope.api.paper_startup import _controller_allowed
    value=dict(plan=a.h.plan(SHA,'c'*64,'approved:build'),events=[dict(state=state,code=code)])
    with pytest.raises(RuntimeError):_controller_allowed(value,SHA,'c'*64,'approved:build')



@pytest.mark.parametrize('extra',[['--startup-request','bad'],['--first-cycle-execution-request','a'*64],['--authority-wait-seconds','61'],
                                 ['--startup-request','a'*64,'--once']])
def test_startup_cli_options_cannot_silently_change_ordinary_admission(extra,capsys):
    from hope.api.paper_service import main
    with pytest.raises(SystemExit) as error:main(['--factory','approved:build','--migrations-dir','/unused','--status-file','/unused']+extra)
    assert error.value.code==2 and 'PAPER_STARTUP_ARGUMENTS_INVALID' in capsys.readouterr().err


def test_ordinary_execution_still_requires_current_guard_before_factory_or_database(tmp_path,monkeypatch,capsys):
    from hope.api import paper_service as api
    migrations=tmp_path/'releases'/SHA/'migrations';migrations.mkdir(parents=True)
    guard=Mock(side_effect=RuntimeError('CURRENT required'));factory=Mock();database=Mock()
    monkeypatch.setattr(api,'paper_operational_guard',guard);monkeypatch.setattr(api,'load_plan_factory',factory)
    monkeypatch.setattr(api,'create_engine',database);monkeypatch.setattr(api,'publish_startup_failure',Mock())
    monkeypatch.setattr(api.signal,'signal',Mock());monkeypatch.setenv('HOPE_DATABASE_URL','postgresql://synthetic-not-connected')
    with pytest.raises(SystemExit):api.main(['--factory','approved:build','--migrations-dir',str(migrations),'--status-file',str(tmp_path/'health')])
    guard.assert_called_once();factory.assert_not_called();database.assert_not_called()



@pytest.mark.skipif(os.name!='posix',reason='Native Linux kernel namespace/image correlation')
@pytest.mark.parametrize('fault',[None,'shared_runtime','image','process','mount','root','root_user'])
def test_controller_cannot_borrow_image_proof_from_a_different_namespace(tmp_path,monkeypatch,fault):
    import subprocess,sys,json
    from pathlib import Path
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
    try:
        manifest=a.h.r._canonical(dict(config=dict(digest='sha256:'+'5'*64)))
        token=dict(controller_runtime_mode='shared' if fault=='shared_runtime' else 'release',deployment_receipt_sha256='c'*64,
                   image_sha256=a.h.r._digest(manifest),stage_process_sha256=a.h.digest((child.pid,a.h._process_identity(child.pid))))
        if fault=='process':token['stage_process_sha256']='0'*64
        receipt=dict(image_sha256=token['image_sha256'],engine='podman',container_id='d'*64)
        monkeypatch.setattr(a.h.d,'signed',lambda *args:(receipt,'c'*64))
        monkeypatch.setattr(a.h.d,'container_observation',lambda *args:dict(pid=child.pid,container_id='d'*64,config_sha256='0'*64 if fault=='image' else '5'*64))
        monkeypatch.setattr(a.h.r,'_read',lambda *args,**kw:manifest)
        if fault in {'mount','root'}:
            original=Path.stat
            selected='/proc/self/ns/mnt' if fault=='mount' else '/proc/self/root'
            def changed(path,*args,**kw):
                value=original(path,*args,**kw)
                return SimpleNamespace(st_dev=value.st_dev,st_ino=value.st_ino+1) if str(path)==selected else value
            monkeypatch.setattr(Path,'stat',changed)
        if fault=='root_user':monkeypatch.setattr(a.os,'geteuid',lambda:0)
        if fault is None:assert a.h.is_hash(a.namespace_binding(tmp_path,SimpleNamespace(token=token)))
        else:
            with pytest.raises(RuntimeError):a.namespace_binding(tmp_path,SimpleNamespace(token=token))
    finally:
        child.kill();child.wait(timeout=10)



@pytest.mark.skipif(os.name!='posix',reason='Real replacement process cannot reuse another incarnation authority')
def test_replacement_process_cannot_reuse_signed_startup_authority(tmp_path,monkeypatch):
    import subprocess,sys
    case=startup_case(tmp_path,monkeypatch,RUN)
    script="""
import os,sys
from hope.api import startup_authority as a
from hope.api import runtime_integrity as r
r.OWNER=os.geteuid()
try:a.verify_startup(sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5],sys.argv[6],sys.argv[7])
except RuntimeError:raise SystemExit(0)
raise SystemExit(7)
"""
    child=subprocess.run([sys.executable,'-c',script,str(case['root']),SHA,case['startup']['request_sha256'],case['token']['request_sha256'],
                          'c'*64,'approved.factory:build',logical_cycle_key(RUN)],capture_output=True,text=True,timeout=10)
    assert child.returncode==0 and not child.stdout
