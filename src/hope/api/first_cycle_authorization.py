"""Independent, one-use PAPER handoff intent verification; never runs a worker.

The result is an internal verified capability for the audit repository, not a
running-readiness report or permission to bypass financial execution gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import os
from pathlib import Path
import sys
import time

from hope.api import deployment_contract as d, runtime_integrity as r
from hope.api.operations_deployment import _activation_guard, _process_identity, inspect_release
from hope.api.paper_controller import plan, is_hash
from hope.api.runtime_identity import verify_interpreter, verify_listener
from hope.api.runtime_bootstrap import CACHE_PREFIX
from hope.api.release_builder import _source
from hope.infrastructure.paper_history import digest

SCHEMA='hope.paper-first-cycle-handoff.v1'
SCOPE='ONE_PAPER_HANDOFF_INTENT'
ROLE='first-cycle-ed25519.pub'
FIELDS={'schema','scope','request_sha256','release_commit','deployment_receipt_sha256','factory_sha256',
        'stage_process_sha256','controller_process_sha256','controller_argv_sha256','controller_runtime_mode',
        'controller_manifest_sha256','runtime_manifest_sha256','image_sha256','first_cycle_key_sha256',
        'database_sha256','minimum_policy_generation','issued_at','expires_at','max_cycles','paper_only','live_enabled'}
HASH_FIELDS=FIELDS-{'schema','scope','release_commit','controller_runtime_mode','minimum_policy_generation',
                   'issued_at','expires_at','max_cycles','paper_only','live_enabled'}
STAGE_FIELDS={'schema','state','release_commit','image_sha256','container_id','process_sha256','runtime_manifest_sha256',
              'receipt_sha256','host','container','provenance','provider','rollback','expires_at','blockers','execution_authorized'}


def require(condition):
    if not condition:raise RuntimeError('PAPER_FIRST_CYCLE_AUTHORIZATION_UNVERIFIED')


def process_observation():
    pid=os.getpid();before=_process_identity(pid)
    with Path('/proc/self/cmdline').open('rb') as stream:argv=stream.read(65537)
    require(0<len(argv)<=65536 and argv.endswith(b'\0') and _process_identity(pid)==before)
    return dict(process_sha256=digest((pid,before)),argv_sha256=r._digest(argv))


def controller_runtime(root,sha,mode,manifest):
    """Authenticate the controller independently of the staged Python runtime."""
    root=Path(root);release=root/'releases'/sha
    require(sys.flags.isolated and sys.dont_write_bytecode
            and Path(__file__).resolve(strict=True)==release/'src/hope/api/first_cycle_authorization.py')
    python=r.environment_path(root,sha,mode)/'bin/python'
    verify_interpreter(python);inspect_release(release,sha)
    source=r.source_snapshot(release)
    proof=r.attest_runtime(root,sha,mode,expected_manifest=manifest,release=release)
    require(proof['state']==proof['provenance_state']==proof['build_state']=='VERIFIED')
    pid=os.getpid();identity=_process_identity(pid)
    with Path('/proc/self/cmdline').open('rb') as stream:argv=stream.read(65537)
    require(0<len(argv)<=65536 and argv.endswith(b'\0'))
    arguments=[os.fsdecode(value) for value in argv[:-1].split(b'\0')]
    require(len(arguments)>=6 and arguments[:5]==[str(python),'-I','-B','-X',CACHE_PREFIX]
            and '--stage-only' not in arguments)
    entry=Path(arguments[5]);relative=entry.relative_to(release).as_posix()
    require(entry.parent==release/'deploy/operations' and entry.suffix=='.py'
            and entry.resolve(strict=True)==entry)
    approved=_source(release,sha)
    require(relative in approved and r._read(entry,r.MAX_FILE_BYTES)==approved[relative])
    verify_listener(pid,python,identity=_process_identity,managed=False,expected_identity=identity,expected_arguments=arguments[1:])
    require(r.source_snapshot(release)==source and _source(release,sha)==approved)
    return {key:proof[key] for key in ('manifest_sha256','inventory_sha256','provenance_sha256','build_sha256')}


def _stage(proof,token,instant):
    require(isinstance(proof,dict) and set(proof)==STAGE_FIELDS
            and proof['schema']=='hope.staged-deployment-observation.v1' and proof['state']=='STAGED'
            and proof['execution_authorized'] is False and proof['blockers']==[]
            and type(proof['expires_at']) is int and instant<proof['expires_at']
            and proof['release_commit']==token['release_commit']
            and proof['receipt_sha256']==token['deployment_receipt_sha256']
            and proof['process_sha256']==token['stage_process_sha256']
            and proof['runtime_manifest_sha256']==token['runtime_manifest_sha256']
            and proof['image_sha256']==token['image_sha256']
            and all(proof[key]=='VERIFIED' for key in ('host','container','provenance','provider','rollback'))
            and all(is_hash(proof[key]) for key in ('container_id','process_sha256','image_sha256','runtime_manifest_sha256')))
    # Fresh heartbeat expiry may advance; immutable identity must not change.
    return {key:value for key,value in proof.items() if key!='expires_at'}


def _authority(root,now,minimum):
    folder=root/'image-evidence'
    policy=d._policy(folder/'policy.json',folder/'policy-ed25519.pub',now=now)
    require(policy['generation']>=minimum)
    key=root/'runtime-trust'/ROLE;key_hash=d._key_digest(key)
    others={d._key_digest(root/'runtime-trust'/name) for name in (
        'deployments-ed25519.pub','providers-ed25519.pub','data-acceptance-ed25519.pub','rollback-ed25519.pub',
        'artifacts-ed25519.pub','releases-ed25519.pub','builders-ed25519.pub')}
    others|={d._key_digest(folder/name) for name in ('images-ed25519.pub','policy-ed25519.pub')}
    require(key_hash not in others and key_hash not in policy['revoked_keys'])
    return key_hash,policy


@dataclass(frozen=True)
class VerifiedHandoff:
    authorization_sha256: str
    evidence_sha256: str
    token: dict
    authorizer_key_sha256: str
    policy_authority_sha256: str
    policy_generation: int
    expires_at: int
    _refresh: object

    def assert_current(self):
        current=self._refresh()
        require(current.authorization_sha256==self.authorization_sha256
                and current.evidence_sha256==self.evidence_sha256 and current.token==self.token
                and current.authorizer_key_sha256==self.authorizer_key_sha256
                and current.policy_authority_sha256==self.policy_authority_sha256
                and current.policy_generation>=self.policy_generation)
        return current


def verify(root,sha,request,receipt,factory,*,expected_authorization=None,stage_probe=d.inspect_staged_deployment,
           runtime_probe=controller_runtime,process_probe=process_observation,clock=lambda:int(time.time())):
    """Read-only verification of separately supplied independent authority."""
    root=Path(root);pinned=plan(sha,receipt,factory);require(is_hash(request))
    require(expected_authorization is None or is_hash(expected_authorization))
    with _activation_guard(root,shared=True):
        root_identity=r._trusted(root,directory=True);now=clock()
        reference=root/'first-cycle-authorizations'/f'{request}.ref'
        selected=r._read(reference,64).decode('ascii');require(is_hash(selected))
        require(expected_authorization is None or selected==expected_authorization)
        reference_identity=r._trusted(reference)
        path=reference.parent/f'{selected}.json';key=root/'runtime-trust'/ROLE
        token,token_hash=d.signed(path,key)
        require(token_hash==selected and isinstance(token,dict) and set(token)==FIELDS
                and token['schema']==SCHEMA and token['scope']==SCOPE
                and token['request_sha256']==request and token['release_commit']==sha
                and token['deployment_receipt_sha256']==receipt and token['factory_sha256']==digest(factory)
                and token['paper_only'] is True and token['live_enabled'] is False
                and type(token['max_cycles']) is int and token['max_cycles']==1
                and token['controller_runtime_mode'] in {'shared','release'}
                and type(token['minimum_policy_generation']) is int and token['minimum_policy_generation']>0
                and all(is_hash(token[key]) for key in HASH_FIELDS))
        d.fresh(token,now,60)
        authorizer,policy=_authority(root,now,token['minimum_policy_generation'])
        process=process_probe()
        require(process==dict(process_sha256=token['controller_process_sha256'],argv_sha256=token['controller_argv_sha256'])
                and token['controller_process_sha256']!=token['stage_process_sha256'])
        controller=runtime_probe(root,sha,token['controller_runtime_mode'],token['controller_manifest_sha256'])
        require(controller['manifest_sha256']==token['controller_manifest_sha256'])
        stage=stage_probe(root,sha,expected_receipt=receipt,expected_factory=factory)
        stage_identity=_stage(stage,token,clock())
        closing=clock();d.fresh(token,closing,60)
        closing_authorizer,closing_policy=_authority(root,closing,token['minimum_policy_generation'])
        require(closing_authorizer==authorizer and closing_policy==policy)
        require(runtime_probe(root,sha,token['controller_runtime_mode'],token['controller_manifest_sha256'])==controller
                and process_probe()==process)
        closing_stage=stage_probe(root,sha,expected_receipt=receipt,expected_factory=factory)
        require(_stage(closing_stage,token,clock())==stage_identity)
        require(r._trusted(reference)==reference_identity and r._read(reference,64).decode('ascii')==selected
                and d.signed(path,key)==(token,selected) and r._trusted(root,directory=True)==root_identity)
        finished=clock();d.fresh(token,finished,60)
        require(_authority(root,finished,token['minimum_policy_generation'])==(authorizer,policy))
        require(r._trusted(reference)==reference_identity and r._read(reference,64).decode('ascii')==selected)
        finished=clock();d.fresh(token,finished,60)
        require(now<=finished<min(closing_policy['expires_at'],closing_stage['expires_at']))
        policy_authority=d._key_digest(root/'image-evidence/policy-ed25519.pub')
        evidence=digest(dict(token=selected,controller=controller,stage=stage_identity,authorizer=authorizer,policy_authority=policy_authority))
    def refresh():
        return verify(root,sha,request,receipt,factory,expected_authorization=selected,stage_probe=stage_probe,
                      runtime_probe=runtime_probe,process_probe=process_probe,clock=clock)
    return VerifiedHandoff(selected,evidence,MappingProxyType(dict(token)),authorizer,policy_authority,closing_policy['generation'],
                           min(token['expires_at'],closing_policy['expires_at'],closing_stage['expires_at']),refresh)


def observation(root,sha,request,receipt,factory,**options):
    report=dict(schema='hope.paper-handoff-observation.v1',state='UNVERIFIED',code='INDEPENDENT_HANDOFF_AUTHORITY_REQUIRED',
                release_commit=None,authorization_sha256=None,request_sha256=None,expires_at=None,execution_authorized=False)
    try:
        proof=verify(root,sha,request,receipt,factory,**options)
        report.update(state='AUTHORITY_VERIFIED',code='HANDOFF_INTENT_ONLY',release_commit=sha,
                      authorization_sha256=proof.authorization_sha256,request_sha256=request,expires_at=proof.expires_at)
    except FileNotFoundError:pass
    except Exception:report.update(state='BLOCKED',code='HANDOFF_AUTHORITY_UNVERIFIED')
    return report
