"""Distinct independent bootstrap and one-cycle authorities; no issuer or launcher."""
from dataclasses import dataclass
from datetime import datetime,UTC
from pathlib import Path
from types import MappingProxyType
import time
import os

from hope.api import first_cycle_authorization as h
from hope.api.operations_deployment import _activation_guard
from hope.application.jobs import create_scheduled_job_run
from hope.infrastructure.paper_service import logical_cycle_key

STARTUP_ROLE='startup-ed25519.pub'
EXECUTION_ROLE='first-cycle-execution-ed25519.pub'
COMMON={'schema','scope','request_sha256','handoff_authorization_sha256','release_commit','controller_process_sha256',
        'database_sha256','first_cycle_key_sha256','minimum_policy_generation','issued_at','expires_at','max_cycles','paper_only','live_enabled'}
STARTUP_FIELDS=COMMON|{'schema_catalog_sha256','controller_namespace_sha256'}
EXECUTION_FIELDS=COMMON|{'startup_authorization_sha256','job_run_id','job_key','scheduled_for'}


def roles(root,base,instant,minimum):
    intent_key,policy=h._authority(root,instant,minimum)
    existing={h.d._key_digest(root/'runtime-trust'/name) for name in (
        'deployments-ed25519.pub','providers-ed25519.pub','data-acceptance-ed25519.pub','rollback-ed25519.pub',
        'artifacts-ed25519.pub','releases-ed25519.pub','builders-ed25519.pub')}
    existing|={h.d._key_digest(root/'image-evidence'/name) for name in ('images-ed25519.pub','policy-ed25519.pub')}
    existing.add(intent_key)
    startup=h.d._key_digest(root/'runtime-trust'/STARTUP_ROLE)
    execution=h.d._key_digest(root/'runtime-trust'/EXECUTION_ROLE)
    h.require(startup!=execution and not {startup,execution}&existing
              and not {startup,execution}&set(policy['revoked_keys']) and intent_key==base.authorizer_key_sha256)
    return startup,execution,policy


def namespace_binding(root,intent):
    """Controller must share the independently approved image's real namespaces.

    An approved stage image alone never authenticates a different host worker.
    No environment, mounts list, credential or unrestricted inspect is exposed.
    """
    root=Path(root);token=intent.token
    h.require(token['controller_runtime_mode']=='release')
    receipt,receipt_hash=h.d.signed(root/'deployment-receipts'/(token['deployment_receipt_sha256']+'.json'),root/'runtime-trust/deployments-ed25519.pub')
    h.require(receipt_hash==token['deployment_receipt_sha256'] and receipt['image_sha256']==token['image_sha256'])
    observed=h.d.container_observation(receipt['engine'],receipt['container_id']);pid=observed['pid']
    identity=h._process_identity(pid)
    h.require(h.digest((pid,identity))==token['stage_process_sha256'])
    payload=h.r._read(root/'image-evidence/oci/blobs/sha256'/token['image_sha256'],65536)
    h.require(h.r._digest(payload)==token['image_sha256'])
    manifest=h.d.strict_json(payload)
    h.require(observed['config_sha256']==manifest['config']['digest'][7:])
    current=Path('/proc/self');stage=Path('/proc')/str(pid)
    namespace={}
    for name in ('mnt','pid','user','net'):
        own=(current/'ns'/name).stat();other=(stage/'ns'/name).stat()
        h.require((own.st_dev,own.st_ino)==(other.st_dev,other.st_ino))
        namespace[name]=(own.st_dev,own.st_ino)
    own=(current/'root').stat();other=(stage/'root').stat()
    h.require((own.st_dev,own.st_ino)==(other.st_dev,other.st_ino)
              and stage.stat().st_uid==os.geteuid() and os.geteuid()!=0 and h._process_identity(pid)==identity)
    return h.digest(dict(namespaces=namespace,root=(own.st_dev,own.st_ino),uid=os.geteuid(),
                         container_id=observed['container_id'],image_sha256=token['image_sha256']))


def document(root,kind,request,expected=None):
    h.require(h.is_hash(request) and (expected is None or h.is_hash(expected)))
    reference=root/('startup-authorizations' if kind=='startup' else 'execution-authorizations')/f'{request}.ref'
    selected=h.r._read(reference,64).decode('ascii');h.require(h.is_hash(selected) and (expected is None or expected==selected))
    identity=h.r._trusted(reference);path=reference.parent/f'{selected}.json'
    role=STARTUP_ROLE if kind=='startup' else EXECUTION_ROLE
    value,digest=h.d.signed(path,root/'runtime-trust'/role);h.require(digest==selected)
    def close():
        h.require(h.r._trusted(reference)==identity and h.r._read(reference,64).decode('ascii')==selected
                  and h.d.signed(path,root/'runtime-trust'/role)==(value,selected))
    return value,selected,close


@dataclass(frozen=True)
class Permit:
    kind: str
    authorization_sha256: str
    evidence_sha256: str
    token: object
    base: object
    authorizer_key_sha256: str
    policy_authority_sha256: str
    policy_generation: int
    expires_at: int
    _refresh: object

    def assert_current(self):
        current=self._refresh()
        h.require(current.kind==self.kind and current.authorization_sha256==self.authorization_sha256
                  and current.evidence_sha256==self.evidence_sha256 and current.token==self.token
                  and current.authorizer_key_sha256==self.authorizer_key_sha256
                  and current.policy_authority_sha256==self.policy_authority_sha256
                  and current.policy_generation>=self.policy_generation)
        return current

    @property
    def handoff(self):return self.base if self.kind=='startup' else self.base.base

    @property
    def run(self):
        h.require(self.kind=='execution')
        return authorized_run(self.token)


def authorized_run(value):
    h.require(isinstance(value['job_key'],str) and 0<len(value['job_key'])<=256
              and isinstance(value['scheduled_for'],str) and len(value['scheduled_for'])<=40)
    instant=datetime.fromisoformat(value['scheduled_for'])
    h.require(instant.tzinfo is not None and instant.utcoffset()==UTC.utcoffset(instant)
              and instant.isoformat()==value['scheduled_for'])
    run=create_scheduled_job_run(value['job_key'],instant)
    h.require(run.job_key==value['job_key'] and str(run.job_run_id)==value['job_run_id'] and logical_cycle_key(run)==value['first_cycle_key_sha256'])
    return run


def _validate(value,kind,base,request,instant):
    fields=STARTUP_FIELDS if kind=='startup' else EXECUTION_FIELDS
    intent=base if kind=='startup' else base.base
    h.require(isinstance(value,dict) and set(value)==fields
        and value['schema']=='hope.paper-'+kind+'-authority.v1'
        and value['scope']==('BOOTSTRAP_ONLY' if kind=='startup' else 'ONE_CERTIFIED_PAPER_CYCLE')
        and value['request_sha256']==request and value['handoff_authorization_sha256']==intent.authorization_sha256
        and value['release_commit']==intent.token['release_commit']
        and all(value[key]==intent.token[key] for key in ('controller_process_sha256','database_sha256','first_cycle_key_sha256'))
        and value['paper_only'] is True and value['live_enabled'] is False
        and type(value['max_cycles']) is int and value['max_cycles']==(0 if kind=='startup' else 1)
        and type(value['minimum_policy_generation']) is int and value['minimum_policy_generation']>0)
    h.d.fresh(value,instant,60)
    if kind=='startup':h.require(h.is_hash(value['schema_catalog_sha256']) and h.is_hash(value['controller_namespace_sha256']))
    else:
        h.require(value['startup_authorization_sha256']==base.authorization_sha256)
        authorized_run(value)


def _verified(root,kind,request,base,expected,refresh,clock,subject_probe=None):
    root=Path(root)
    with _activation_guard(root,shared=True):
        before=h.r._trusted(root,directory=True);instant=clock();base=base.assert_current()
        token,selected,close=document(root,kind,request,expected)
        _validate(token,kind,base,request,instant)
        intent=base if kind=='startup' else base.base
        keys=roles(root,intent,instant,token['minimum_policy_generation'])
        subject=None if subject_probe is None else subject_probe(base)
        h.require(kind!='startup' or subject==token['controller_namespace_sha256'])
        policy_authority=h.d._key_digest(root/'image-evidence/policy-ed25519.pub')
        close();base.assert_current();finished=clock();_validate(token,kind,base,request,finished)
        h.require(subject_probe is None or subject_probe(base)==subject)
        h.require(roles(root,intent,finished,token['minimum_policy_generation'])==keys
                  and h.r._trusted(root,directory=True)==before and instant<=finished)
        close();finished=clock();h.d.fresh(token,finished,60)
        expiry=min(token['expires_at'],base.expires_at,keys[2]['expires_at'])
        h.require(finished<expiry and h.d._key_digest(root/'image-evidence/policy-ed25519.pub')==policy_authority)
        evidence=h.digest(dict(kind=kind,authorization=selected,base=base.evidence_sha256,
                              startup_key=keys[0],execution_key=keys[1],policy_authority=policy_authority,subject=subject))
    return Permit(kind,selected,evidence,MappingProxyType(dict(token)),base,keys[0 if kind=='startup' else 1],
                  policy_authority,keys[2]['generation'],expiry,lambda:refresh(selected))


def verify_startup(root,sha,request,handoff_request,receipt,factory,cycle_key,*,expected=None,intent_verifier=h.verify,namespace_probe=namespace_binding,clock=lambda:int(time.time())):
    root=Path(root)
    preliminary,_,close=document(root,'startup',request,expected)
    h.require(isinstance(preliminary,dict) and set(preliminary)==STARTUP_FIELDS
              and type(preliminary['minimum_policy_generation']) is int and preliminary['minimum_policy_generation']>0)
    h.d.fresh(preliminary,clock(),60)
    from types import SimpleNamespace
    intent_key,_=h._authority(root,clock(),preliminary['minimum_policy_generation'])
    roles(root,SimpleNamespace(authorizer_key_sha256=intent_key),clock(),preliminary['minimum_policy_generation']);close()
    base=intent_verifier(root,sha,handoff_request,receipt,factory)
    h.require(base.token['first_cycle_key_sha256']==cycle_key and base.token['controller_runtime_mode']=='release'
              and base.token['controller_manifest_sha256']==base.token['runtime_manifest_sha256'])
    def refresh(selected):
        return verify_startup(root,sha,request,handoff_request,receipt,factory,cycle_key,expected=selected,intent_verifier=intent_verifier,namespace_probe=namespace_probe,clock=clock)
    return _verified(root,'startup',request,base,expected,refresh,clock,lambda intent:namespace_probe(root,intent))


def verify_execution(root,startup,request,*,expected=None,clock=lambda:int(time.time())):
    h.require(type(startup) is Permit and startup.kind=='startup')
    def refresh(selected):return verify_execution(root,startup,request,expected=selected,clock=clock)
    return _verified(root,'execution',request,startup,expected,refresh,clock)
