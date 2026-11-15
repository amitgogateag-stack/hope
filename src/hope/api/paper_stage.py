"""Non-executing PAPER bootstrap heartbeat, never a trading/activation path."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import time

from hope.api.runtime_integrity import _sha
from hope.api.runtime_bootstrap import CACHE_PREFIX
from hope.api.snapshot_contract import strict_json
from hope.api.paper_controller import plan,is_hash
from hope.infrastructure.durable_file import bounded_regular_read
from hope.infrastructure.paper_history import publisher_lease,publisher_observation
from hope.infrastructure.paper_service import publish_service_status
from hope.infrastructure.signal_stop import SignalStop


def require(value):
    if not value:raise RuntimeError('PAPER_STAGE_UNVERIFIED')


def status_path(root):return Path(root)/'staging/paper-stage.json'


def activation_snapshot(root):
    """Use the established journal validator; never repair or adopt an intent."""
    from hope.api.dashboard_activation import Layout,_record
    from hope.api.operations_deployment import inspect_activation_recovery
    require(inspect_activation_recovery(Path(root))['state']=='ABSENT')
    value=_record(Layout(root=Path(root)))
    require(value is None or value['phase']=='COMMITTED')
    return value


def arguments(sha,manifest,factory,request,timeout):
    """Exact bootstrap argv suffix, also bound by the independent receipt."""
    _sha(sha);plan(sha,'0'*64,factory)
    require(is_hash(manifest) and is_hash(request) and type(timeout) is int and 1<=timeout<=300)
    return ['--approved-runtime-manifest',manifest,'--runtime-mode','release','--stage-only',
            '--factory',factory,'--stage-request',request,'--stage-timeout',str(timeout)]


def staged_process_observation(root,container,receipt,provider,*,now=None):
    """Bind live WAITING evidence to actual kernel PID, namespace and argv."""
    from hope.api.operations_deployment import _process_identity
    from hope.infrastructure.paper_history import digest
    root=Path(root);pid=container['pid'];process=Path('/proc')/str(pid)
    before=_process_identity(pid);identity=digest((pid,before))
    path=status_path(root);parent=path.parent.lstat();owner=process.stat().st_uid
    require(stat.S_ISDIR(parent.st_mode) and not parent.st_mode&0o022 and parent.st_uid==owner)
    identities=[]
    for target in (path.parent,path,Path(str(path)+'.history.lock')):
        host=target.lstat();namespace=process/'root/opt/hope-operations'/target.relative_to(root)
        inside=namespace.lstat()
        require((host.st_dev,host.st_ino)==(inside.st_dev,inside.st_ino) and host.st_uid==owner)
        identities.append((host.st_dev,host.st_ino))
    raw=strict_json(bounded_regular_read(path,4096))
    require(set(raw)=={'schema','state','release_commit','factory','request','timeout','generated_at',
                      'execution_authorized','publisher_instance','publisher_identity_sha256'}
            and raw['schema']=='hope.paper-stage.v1' and raw['state']=='WAITING'
            and raw['release_commit']==receipt['release_commit'] and raw['execution_authorized'] is False
            and raw['publisher_identity_sha256']==identity
            and raw['factory']==provider['factory_module']+':'+provider['factory_name'])
    instant=datetime.now(timezone.utc) if now is None else now
    stamp=datetime.fromisoformat(raw['generated_at'])
    require(stamp.tzinfo is not None and 0<=(instant-stamp).total_seconds()<=5)
    observed=publisher_observation(path,expected_instance=raw['publisher_instance'],expected_identity=identity)
    require(observed['state']=='VERIFIED' and observed['identity_sha256']==receipt['process_sha256']==identity)
    with (process/'cmdline').open('rb') as stream:argv=stream.read(65537)
    sha=receipt['release_commit']
    expected=[str(PurePosixPath('/opt/hope-operations/release-venvs')/sha/'bin/python'),'-I','-B','-X',CACHE_PREFIX,
              str(PurePosixPath('/opt/hope-operations/releases')/sha/'deploy/operations/paper_release.py')]
    expected+=arguments(sha,receipt['runtime_manifest_sha256'],raw['factory'],raw['request'],raw['timeout'])
    require(argv==b'\0'.join(os.fsencode(value) for value in expected)+b'\0' and _process_identity(pid)==before)
    # Heartbeats may advance; immutable ownership/request fields must not.
    after=strict_json(bounded_regular_read(path,4096))
    require({key:value for key,value in after.items() if key!='generated_at'}==
            {key:value for key,value in raw.items() if key!='generated_at'})
    require(publisher_observation(path,expected_instance=raw['publisher_instance'],expected_identity=identity)['state']=='VERIFIED'
            and _process_identity(pid)==before)
    # Status publication atomically replaces its inode every heartbeat. Its
    # namespace binding is checked at each observation, but only directory and
    # lifetime lock inodes are immutable across the verification window.
    return dict(binding=(identity,raw['request'],raw['publisher_instance'],(identities[0],identities[2])),
                expires_at=int(stamp.timestamp())+5)


def run_stage(root,sha,factory,request,*,timeout=300,stop=None,clock=time.monotonic,publish=publish_service_status):
    """Publish WAITING only. No DB, factory import, token consumption or exec."""
    arguments(sha,'0'*64,factory,request,timeout)
    root=Path(root);require(root.is_absolute() and root.resolve(strict=True)==root)
    require(os.environ.get('HOPE_LIVE_ENABLED')=='false' and os.environ.get('HOPE_DEPLOYED_COMMIT')==sha)
    path=status_path(root)
    require(path.parent.resolve(strict=True)==path.parent)
    stop=stop or SignalStop();deadline=clock()+timeout
    with publisher_lease(path) as lease:
        def emit(state):
            value=dict(schema='hope.paper-stage.v1',state=state,release_commit=sha,factory=factory,
                       request=request,timeout=timeout,generated_at=datetime.now(timezone.utc).isoformat(),execution_authorized=False)
            lease.publish(path,value,publish)
        try:
            while not stop.is_set() and clock()<deadline:
                emit('WAITING');stop.wait(min(1,max(0,deadline-clock())))
        finally:
            emit('STOPPED')
    return 0


def main(argv=None,*,release):
    parser=argparse.ArgumentParser(description='PAPER stage only; never imports a factory or starts execution')
    parser.add_argument('--factory',required=True)
    parser.add_argument('--stage-request',required=True)
    parser.add_argument('--stage-timeout',type=int,default=300)
    args=parser.parse_args(argv)
    stop=SignalStop()
    for signum in (signal.SIGTERM,signal.SIGINT):signal.signal(signum,lambda *_:stop.set())
    try:
        release=Path(release);sha=_sha(release.name);root=release.parent.parent
        require(release==root/'releases'/sha)
        return run_stage(root,sha,args.factory,args.stage_request,timeout=args.stage_timeout,stop=stop)
    except Exception:
        parser.exit(2,'PAPER_STAGE_UNVERIFIED\n')
