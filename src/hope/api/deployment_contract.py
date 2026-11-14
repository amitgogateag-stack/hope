"""Read-only EC2 integration contract built on existing runtime/image preflight.

No AWS API, deployment, factory import, credential read, approval generation or
service activation. Missing independent approval is UNKNOWN, never readiness.
"""
from __future__ import annotations
from datetime import datetime,timezone
import ast
import hmac
from pathlib import Path
import os
import re
import sys
import time

from hope.api import runtime_integrity as r
from hope.api.artifact_approval import verify_document
from hope.api.diagnostic_io import bounded_capture
from hope.api.release_image import verify_image,_key_digest,_policy
from hope.api.snapshot_contract import strict_json
from hope.api.operations_deployment import _activation_guard

ENV={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','HOME':'/nonexistent'}
HASH=re.compile('[0-9a-f]{64}')
FACTORY=re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*',re.ASCII)
FORMAT='\n'.join('{{json .'+field+'}}' for field in (
    'Id','Image','State.Running','State.Pid','HostConfig.Privileged','HostConfig.ReadonlyRootfs','HostConfig.NetworkMode','Config.User'))


def require(value):
    if not value:raise RuntimeError('DEPLOYMENT_EVIDENCE_INVALID')


def signed(path,key):
    payload=r._read(path,65536)
    return verify_document(path,path.with_suffix('.sig'),key,expected_digest=r._digest(payload)),r._digest(payload)


def fresh(value,now,maximum=3600):
    require(type(value['issued_at']) is int and type(value['expires_at']) is int
        and value['issued_at'] <= now < value['expires_at']
        and 0 < value['expires_at']-value['issued_at'] <= maximum)


def authorities(root,folder,now):
    policy=_policy(folder/'policy.json',folder/'policy-ed25519.pub',now=now)
    roles={_key_digest(root/'runtime-trust'/name) for name in (
        'deployments-ed25519.pub','providers-ed25519.pub','data-acceptance-ed25519.pub','rollback-ed25519.pub')}
    require(len(roles)==4 and not roles & set(policy['revoked_keys'])
        and not roles & {_key_digest(folder/'images-ed25519.pub'),_key_digest(folder/'policy-ed25519.pub')})
    return roles


def host_observation(*,connection=None,capture=bounded_capture):
    """Local metadata only; a DB version is unknown without a read-only connection."""
    systemd=capture(['/usr/bin/systemctl','--version'],timeout=3,max_output=4096,env=ENV)
    match=re.match(r'systemd (\d+)\b',systemd);require(match)
    postgres=None
    if connection is not None:
        from sqlalchemy import text
        require(connection.execute(text('SHOW transaction_read_only')).scalar_one()=='on')
        postgres=int(connection.execute(text('SHOW server_version_num')).scalar_one())//10000
    from hope.api.operations_deployment import _process_identity
    # Identifiers and OS bytes are hashed locally and never returned verbatim.
    return dict(host_sha256=r._digest(r._read(Path('/etc/machine-id'),128,system=True)),
        boot_sha256=r._digest(_process_identity(os.getpid())[0].encode()),
        os_release_sha256=r._digest(r._read(Path('/etc/os-release').resolve(strict=True),65536,system=True)),
        python_version=list(sys.version_info[:2]),postgres_major=postgres,systemd_major=int(match[1]))


def container_observation(engine,container_id,*,capture=bounded_capture):
    """Ask for eight public fields; never inspect env, mounts or credentials."""
    require(engine in {'docker','podman'} and isinstance(container_id,str) and HASH.fullmatch(container_id))
    binary=Path('/usr/bin')/engine;r._trusted(binary.resolve(strict=True),system=True)
    output=capture([str(binary),'inspect','--type','container','--format',FORMAT,container_id],
        timeout=3,max_output=4096,env=ENV)
    fields=output.strip().splitlines();require(len(fields)==8)
    values=[strict_json(value.encode()) for value in fields]
    identity,image,running,pid,privileged,readonly,network,user=values
    require(identity==container_id and isinstance(image,str) and re.fullmatch('sha256:[0-9a-f]{64}',image)
        and running is True and type(pid) is int and pid>0 and privileged is False and readonly is True
        and isinstance(network,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',network) and network not in {'host','container'}
        and user=='65532:65532')
    return dict(container_id=identity,config_sha256=image[7:],pid=pid,network=network)


def process_observation(container,receipt):
    from hope.api.operations_deployment import _process_identity
    pid=container['pid'];before=_process_identity(pid);process=Path('/proc')/str(pid)
    logical=Path('/opt/hope-operations/release-venvs')/receipt['release_commit']/'bin/python'
    executable=process/'root'/logical.relative_to('/')
    selected=executable.stat();kernel=(process/'exe').stat()
    require((selected.st_dev,selected.st_ino)==(kernel.st_dev,kernel.st_ino))
    python=r._fingerprint(executable,time.monotonic()+r.MAX_SECONDS,system=True)[0]
    with (process/'cmdline').open('rb') as stream:arguments=stream.read(65537)
    require(0<len(arguments)<=65536 and arguments.endswith(b'\0') and arguments.split(b'\0')[0]==os.fsencode(logical)
        and python==receipt['python_sha256'] and r._digest(arguments)==receipt['argv_sha256']
        and _process_identity(pid)==before and (process/'exe').stat().st_ino==kernel.st_ino
        and executable.stat().st_ino==selected.st_ino)
    return r._digest(r._canonical((pid,before)))


def runtime_observation(root,sha,container,receipt,*,node_stat=None):
    """Require reviewed host source/runtime bindings, not a separate lookalike.

    The existing inventory/attestation owns dependency validation. Every
    namespace node must be that same approved inode; nested substitution fails.
    """
    deadline=time.monotonic()+r.MAX_SECONDS
    node_stat=node_stat or Path.lstat
    environment,_,_,before=r._inventory(root,sha,'release',deadline)
    source=r.source_snapshot(root/'releases'/sha)
    from hope.api.release_builder import _source
    tracked=_source(root/'releases'/sha,sha)
    source.update({str(root/'releases'/sha/name):r._trusted(root/'releases'/sha/name) for name in tracked})
    proof=r.attest_runtime(root,sha,'release',expected_manifest=receipt['runtime_manifest_sha256'])
    image=strict_json(r._read(root/'image-evidence/approval.json',65536))
    require(proof['state']==proof['provenance_state']==proof['build_state']=='VERIFIED'
        and proof['build_sha256']==image['release_approval_sha256']
        and proof['provenance_sha256']==image['hashes']['package-provenance.json'])
    namespace=Path('/proc')/str(container['pid'])/'root/opt/hope-operations'
    nodes={**before[0],**before[1],**{key:value[1] for key,value in before[2].items()}}
    expected={str(environment/key):identity for key,identity in nodes.items()}
    expected.update(source)
    for name,identity in expected.items():
        require(time.monotonic()<=deadline)
        actual=namespace/Path(name).relative_to(root)
        require(r._identity(node_stat(actual))==identity)
    require(r._inventory(root,sha,'release',deadline)[3]==before
        and all(r._trusted(Path(name),directory=Path(name).is_dir())==identity for name,identity in source.items())
        and _source(root/'releases'/sha,sha)==tracked)
    return proof


def provider_contract(root,sha,digest,now):
    path=root/'paper-provider'/f'{sha}.json';key=root/'runtime-trust/providers-ed25519.pub'
    value,actual=signed(path,key)
    fields={'schema','scope','release_commit','factory_module','factory_name','factory_sha256','dataset_sha256','calendar_sha256','markets','issued_at','expires_at'}
    require(actual==digest and set(value)==fields and value['schema']=='hope.paper-provider.v1'
        and value['scope']=='APPROVED_REAL_DATA' and value['release_commit']==sha and value['markets']==['INDIA','USA'])
    fresh(value,now,86400)
    for field in ('factory_sha256','dataset_sha256','calendar_sha256'):require(HASH.fullmatch(value[field]))
    require(isinstance(value['factory_module'],str) and all(part.isidentifier() for part in value['factory_module'].split('.'))
        and isinstance(value['factory_name'],str) and value['factory_name'].isidentifier())
    # Authenticate the reviewed factory bytes without importing provider code.
    release=root/'releases'/sha
    from hope.api.operations_deployment import inspect_release
    inspect_release(release,sha);before=r.source_snapshot(release)
    factory=release/'src'/Path(*value['factory_module'].split('.')).with_suffix('.py')
    payload=r._read(factory,65536);tree=ast.parse(payload)
    require(factory.resolve(strict=True)==factory and r._digest(payload)==value['factory_sha256']
        and sum(1 for _ in ast.walk(tree))<=10000
        and sum(isinstance(node,ast.FunctionDef) and node.name==value['factory_name'] for node in tree.body)==1
        and r.source_snapshot(release)==before)
    return value


def data_acceptance(root,sha,provider,digest,now):
    value,_=signed(root/'paper-provider'/f'{sha}.acceptance.json',root/'runtime-trust/data-acceptance-ed25519.pub')
    fields={'schema','scope','release_commit','provider_sha256','dataset_sha256','calendar_sha256','issued_at','expires_at',
        'market_data','scheduler','risk','reconciliation','live_enabled'}
    require(set(value)==fields and value['schema']=='hope.real-data-acceptance.v1' and value['scope']=='REAL_DATA'
        and value['release_commit']==sha and value['provider_sha256']==digest
        and value['dataset_sha256']==provider['dataset_sha256'] and value['calendar_sha256']==provider['calendar_sha256']
        and value['market_data'] in {'FRESH_COMPLETE_PIT','VERIFIED_SESSION_CLOSED'} and value['scheduler']=='CURRENT'
        and value['risk']=='CLEAR' and value['reconciliation']=='CLEAR' and value['live_enabled'] is False)
    fresh(value,now,120)
    return value


def rollback_contract(root,sha,digest,now):
    value,actual=signed(root/'deployment-receipts'/f'{digest}.rollback.json',root/'runtime-trust/rollback-ed25519.pub')
    require(actual==digest and set(value)=={'schema','release_commit','previous_release','runtime_mode','manifest_sha256','archive_sha256','issued_at','expires_at'}
        and value['schema']=='hope.rollback-receipt.v1' and value['release_commit']==sha
        and r._sha(value['previous_release'])!=sha and value['runtime_mode'] in {'shared','release'}
        and HASH.fullmatch(value['manifest_sha256']) and HASH.fullmatch(value['archive_sha256']))
    fresh(value,now)
    previous=value['previous_release']
    from hope.api.operations_deployment import inspect_release
    inspect_release(root/'releases'/previous,previous)
    proof=r.attest_runtime(root,previous,value['runtime_mode'],expected_manifest=value['manifest_sha256'])
    require(proof['state']==proof['provenance_state']==proof['build_state']=='VERIFIED'
        and r._fingerprint(root/'release-builds'/f"{previous}-{value['runtime_mode']}.tar",time.monotonic()+r.MAX_SECONDS)[0]==value['archive_sha256'])
    return value


def inspect_deployment(root,sha,*,now=None,expected_receipt=None,expected_factory=None,host_probe=host_observation,container_probe=container_observation,
        process_probe=process_observation,runtime_probe=runtime_observation,host_readiness=None):
    return _inspect_deployment(root,sha,now=now,expected_receipt=expected_receipt,expected_factory=expected_factory,
        host_probe=host_probe,container_probe=container_probe,process_probe=process_probe,runtime_probe=runtime_probe,host_readiness=host_readiness)


def inspect_staged_deployment(root,sha,*,now=None,expected_receipt=None,expected_factory=None,host_probe=host_observation,
        container_probe=container_observation,process_probe=process_observation,runtime_probe=runtime_observation):
    """Authenticate a non-executing bootstrap; never satisfy running admission."""
    return _inspect_deployment(root,sha,now=now,expected_receipt=expected_receipt,expected_factory=expected_factory,
        host_probe=host_probe,container_probe=container_probe,process_probe=process_probe,runtime_probe=runtime_probe,staged=True)


def _inspect_deployment(root,sha,*,now=None,expected_receipt=None,expected_factory=None,host_probe=host_observation,
        container_probe=container_observation,process_probe=process_observation,runtime_probe=runtime_observation,host_readiness=None,staged=False):
    """Reconcile signed approvals with fresh local observations; never activate."""
    root=Path(root);live_clock=now is None;now=int(time.time()) if live_clock else now
    report=dict(schema='hope.deployment-observation.v1',state='UNKNOWN',release_commit=None,image_sha256=None,
        container_id=None,process_sha256=None,runtime_manifest_sha256=None,
        receipt_sha256=None,host='UNKNOWN',container='UNKNOWN',provenance='UNKNOWN',provider='UNKNOWN',rollback='UNKNOWN',
        expires_at=None,blockers=['INDEPENDENT_DEPLOYMENT_RECEIPT_REQUIRED'],execution_authorized=False)
    if staged:report['schema']='hope.staged-deployment-observation.v1'
    blocker='INDEPENDENT_DEPLOYMENT_RECEIPT_REQUIRED'
    try:
        require(expected_receipt is None or (type(expected_receipt) is str and HASH.fullmatch(expected_receipt)))
        require(expected_factory is None or (type(expected_factory) is str and len(expected_factory)<=200
            and FACTORY.fullmatch(expected_factory)))
        with _activation_guard(root,shared=True):
            root_identity=r._trusted(root,directory=True)
            if staged:
                from hope.api.paper_stage import activation_snapshot
                blocker='ACTIVATION_RECOVERY_REQUIRED'
                activation=activation_snapshot(root)
            sha=r._sha(sha);report['release_commit']=sha
            reference=root/'deployment-receipts'/f'{sha}.ref'
            pinned=r._read(reference,64).decode('ascii');require(HASH.fullmatch(pinned))
            require(expected_receipt is None or hmac.compare_digest(pinned,expected_receipt))
            reference_identity=r._trusted(reference)
            receipt_path=root/'deployment-receipts'/f'{pinned}.json'
            value,digest=signed(receipt_path,root/'runtime-trust/deployments-ed25519.pub')
            fields={'schema','scope','release_commit','image_sha256','runtime_manifest_sha256','host_sha256','boot_sha256','container_id',
                'engine','process_sha256','argv_sha256','python_sha256','os_release_sha256','postgres_major','minimum_systemd',
                'provider_sha256','rollback_sha256','minimum_policy_generation','issued_at','expires_at'}
            require(digest==pinned and set(value)==fields and value['schema']=='hope.deployment-receipt.v1' and value['scope']=='PRODUCTION_PAPER'
                and value['release_commit']==sha and type(value['postgres_major']) is int and value['postgres_major'] in {16,17}
                and type(value['minimum_systemd']) is int and value['minimum_systemd']>=249
                and type(value['minimum_policy_generation']) is int and value['minimum_policy_generation']>0)
            for field in fields-{'schema','scope','release_commit','engine','postgres_major','minimum_systemd','minimum_policy_generation','issued_at','expires_at'}:
                require(isinstance(value[field],str) and HASH.fullmatch(value[field]))
            fresh(value,now)
            report.update(state='BLOCKED',receipt_sha256=digest,image_sha256=value['image_sha256'],expires_at=value['expires_at'],blockers=[])
            blocker='SIGNED_PRODUCTION_IMAGE_REQUIRED'
            folder=root/'image-evidence'
            proof=verify_image(folder/'oci',folder/'approval.json',folder/'images-ed25519.pub',folder/'policy.json',
                folder/'policy-ed25519.pub',expected_sha=sha,now=now,minimum_generation=value['minimum_policy_generation'])
            require(proof['image_sha256']==value['image_sha256'] and proof['scope']=='PRODUCTION_PAPER')
            require(_key_digest(root/'runtime-trust/deployments-ed25519.pub') not in
                {_key_digest(folder/'images-ed25519.pub'),_key_digest(folder/'policy-ed25519.pub')})
            approved_roles=authorities(root,folder,now)
            report['provenance']='VERIFIED'
            blocker='APPROVED_REAL_DATA_PROVIDER_AND_ACCEPTANCE_REQUIRED'
            provider=provider_contract(root,sha,value['provider_sha256'],now)
            require(expected_factory is None or hmac.compare_digest(expected_factory,
                provider['factory_module']+':'+provider['factory_name']))
            accepted=data_acceptance(root,sha,provider,value['provider_sha256'],now);report['provider']='VERIFIED'
            blocker='VERIFIED_ROLLBACK_RECEIPT_AND_RUNTIME_REQUIRED'
            rollback_contract(root,sha,value['rollback_sha256'],now);report['rollback']='VERIFIED'
            blocker='COMPATIBLE_HOST_AND_READONLY_DATABASE_OBSERVATION_REQUIRED'
            host=host_probe()
            require(host['python_version']==[3,13] and host['postgres_major']==value['postgres_major']
                and host['systemd_major']>=value['minimum_systemd']
                and all(host[field]==value[field] for field in ('host_sha256','boot_sha256','os_release_sha256')))
            report['host']='VERIFIED'
            blocker='RUNNING_CONTAINER_AND_KERNEL_IDENTITY_REQUIRED'
            observed=container_probe(value['engine'],value['container_id'])
            manifest=strict_json(r._read(folder/'oci/blobs/sha256'/proof['image_sha256'],65536))
            require(observed['config_sha256']==manifest['config']['digest'][7:]
                and process_probe(observed,value)==value['process_sha256'])
            report['container']='VERIFIED'
            report.update(container_id=observed['container_id'],process_sha256=value['process_sha256'])
            blocker='SIGNED_IMAGE_TO_RUNNING_RUNTIME_BINDING_REQUIRED'
            runtime=runtime_probe(root,sha,observed,value)
            require(runtime['manifest_sha256']==value['runtime_manifest_sha256'])
            if staged:
                from hope.api.paper_stage import staged_process_observation
                blocker='NON_EXECUTING_STAGE_PROCESS_REQUIRED'
                stage=staged_process_observation(root,observed,value,provider)
            elif host_readiness is None:
                from hope.api.aws_readiness import inspect_aws_readiness
                host_readiness=inspect_aws_readiness
            if not staged:
                blocker='HOST_RUNTIME_AND_PAPER_READINESS_REQUIRED'
                current=host_readiness(sha,root=root)
                require(current['ready'] is True and type(current['units']['hope-paper.service']['MainPID']) in {str,int}
                    and int(current['units']['hope-paper.service']['MainPID'])==observed['pid'])
            report['runtime_manifest_sha256']=value['runtime_manifest_sha256']
            # Close on all identities: a current process must still match the same
            # independently approved receipt after slow host/readiness observations.
            blocker='CONCURRENT_CHANGE_OR_EXPIRED_ACCEPTANCE'
            closing=int(time.time()) if live_clock else now
            fresh(value,closing)
            require(verify_image(folder/'oci',folder/'approval.json',folder/'images-ed25519.pub',folder/'policy.json',
                folder/'policy-ed25519.pub',expected_sha=sha,now=closing,minimum_generation=value['minimum_policy_generation'])==proof)
            require(authorities(root,folder,closing)==approved_roles)
            require(runtime_probe(root,sha,observed,value)==runtime)
            require(host_probe()==host and container_probe(value['engine'],value['container_id'])==observed
                and process_probe(observed,value)==value['process_sha256']
                and r._trusted(reference)==reference_identity and r._read(reference,64).decode('ascii')==pinned and signed(receipt_path,root/'runtime-trust/deployments-ed25519.pub')[1]==digest)
            provider_contract(root,sha,value['provider_sha256'],closing);data_acceptance(root,sha,provider,value['provider_sha256'],closing)
            rollback_contract(root,sha,value['rollback_sha256'],closing)
            stage_expiry=value['expires_at']
            if staged:
                require(activation_snapshot(root)==activation)
                closing_stage=staged_process_observation(root,observed,value,provider)
                require(closing_stage['binding']==stage['binding'] and closing<closing_stage['expires_at'])
                stage_expiry=closing_stage['expires_at']
            require(r._trusted(root,directory=True)==root_identity)
            expires=min(value['expires_at'],proof['expires_at'],accepted['expires_at'],stage_expiry)
            finished=int(time.time()) if live_clock else now
            require(now<=finished<expires)
            report.update(state='STAGED' if staged else 'VERIFIED',expires_at=expires)
    except FileNotFoundError:
        report['blockers']=[blocker]
    except Exception:
        report.update(state='BLOCKED',blockers=[blocker])
    return report
