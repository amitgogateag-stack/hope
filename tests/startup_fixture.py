"""Ephemeral independent authorities; runtime/stage witnesses remain explicit models."""
from copy import deepcopy
from pathlib import Path
import subprocess
from uuid import uuid4
from first_cycle_fixture import signed_case,SHA,RECEIPT,FACTORY
from hope.api import startup_authority as a
from hope.infrastructure.paper_service import logical_cycle_key


def startup_case(folder,monkeypatch,run,*,database_sha256='4'*64,catalog_sha256='6'*64):
    case=signed_case(folder,monkeypatch);root=case['root']
    for role in ('startup','first-cycle-execution'):
        key=case['private']['first-cycle'].parent/(role+'.key');pub=root/'runtime-trust'/(role+'-ed25519.pub')
        subprocess.run(['/usr/bin/openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,capture_output=True)
        subprocess.run(['/usr/bin/openssl','pkey','-in',str(key),'-pubout','-out',str(pub)],check=True,capture_output=True)
        case['private'][role]=key
    case['token'].update(database_sha256=database_sha256,first_cycle_key_sha256=logical_cycle_key(run),request_sha256=a.h.digest(uuid4().hex),
                         controller_manifest_sha256=case['token']['runtime_manifest_sha256'])
    case['controller']['manifest_sha256']=case['token']['runtime_manifest_sha256']
    intent=case['pin']();common={key:case['token'][key] for key in ('release_commit','controller_process_sha256','database_sha256','first_cycle_key_sha256',
        'minimum_policy_generation','issued_at','expires_at','paper_only','live_enabled')}
    startup=dict(common,schema='hope.paper-startup-authority.v1',scope='BOOTSTRAP_ONLY',max_cycles=0,
        request_sha256=a.h.digest(uuid4().hex),handoff_authorization_sha256=intent,schema_catalog_sha256=catalog_sha256,controller_namespace_sha256='7'*64)
    execution=dict(common,schema='hope.paper-execution-authority.v1',scope='ONE_CERTIFIED_PAPER_CYCLE',max_cycles=1,
        request_sha256=a.h.digest(uuid4().hex),handoff_authorization_sha256=intent,startup_authorization_sha256='0'*64,
        job_run_id=str(run.job_run_id),job_key=run.job_key,scheduled_for=run.scheduled_for.isoformat())
    for kind in ('startup','execution'):(root/(kind+'-authorizations')).mkdir()
    def pin(kind):
        value=startup if kind=='startup' else execution;role='startup' if kind=='startup' else 'first-cycle-execution'
        payload=a.h.r._canonical(value);digest=a.h.r._digest(payload);path=root/(kind+'-authorizations')/(digest+'.json')
        path.write_bytes(payload)
        subprocess.run(['/usr/bin/openssl','pkeyutl','-sign','-rawin','-inkey',str(case['private'][role]),'-in',str(path),'-out',str(path.with_suffix('.sig'))],check=True,capture_output=True)
        (path.parent/(value['request_sha256']+'.ref')).write_text(digest,encoding='ascii')
        return digest
    startup_digest=pin('startup');execution['startup_authorization_sha256']=startup_digest;pin('execution')
    def verify_startup():return a.verify_startup(root,SHA,startup['request_sha256'],case['token']['request_sha256'],RECEIPT,FACTORY,logical_cycle_key(run),intent_verifier=lambda *args,**kw:case['verify'](),namespace_probe=lambda *args:'7'*64)
    def verify_execution(permit=None):return a.verify_execution(root,permit or verify_startup(),execution['request_sha256'])
    return dict(case, startup=startup,execution=execution,pin_authority=pin,verify_startup=verify_startup,verify_execution=verify_execution)
