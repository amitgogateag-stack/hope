"""Synthetic witness models and ephemeral native signing authority for tests."""
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import time
from unittest.mock import Mock

from hope.api import first_cycle_authorization as a, runtime_integrity as r

SHA='a'*40
REQUEST='b'*64
RECEIPT='c'*64
FACTORY='approved.factory:build'


def model(now):
    token=dict(schema=a.SCHEMA,scope=a.SCOPE,request_sha256=REQUEST,release_commit=SHA,
        deployment_receipt_sha256=RECEIPT,factory_sha256=a.digest(FACTORY),stage_process_sha256='d'*64,
        controller_process_sha256='e'*64,controller_argv_sha256='f'*64,controller_runtime_mode='release',
        controller_manifest_sha256='1'*64,runtime_manifest_sha256='2'*64,image_sha256='3'*64,
        database_sha256='4'*64,first_cycle_key_sha256='5'*64,minimum_policy_generation=7,
        issued_at=now-1,expires_at=now+30,max_cycles=1,paper_only=True,live_enabled=False)
    stage=dict(schema='hope.staged-deployment-observation.v1',state='STAGED',release_commit=SHA,
        image_sha256='3'*64,container_id='6'*64,process_sha256='d'*64,runtime_manifest_sha256='2'*64,
        receipt_sha256=RECEIPT,host='VERIFIED',container='VERIFIED',provenance='VERIFIED',provider='VERIFIED',
        rollback='VERIFIED',expires_at=now+5,blockers=[],execution_authorized=False)
    controller=dict(manifest_sha256='1'*64,inventory_sha256='7'*64,provenance_sha256='8'*64,build_sha256='9'*64)
    policy=dict(generation=7,expires_at=now+300,revoked_keys=[])
    return token,stage,controller,policy


def signed_case(folder,monkeypatch,*,receipt=RECEIPT):
    """Real signatures/kernel owner; stage and runtime attestations are modeled."""
    import pytest
    if os.name!='posix':pytest.skip('Native POSIX/OpenSSL signatures required')
    monkeypatch.setattr(r,'OWNER',os.geteuid())
    root=folder/'operations';root.mkdir();keys=folder/'ephemeral-keys';keys.mkdir()
    trust=root/'runtime-trust';trust.mkdir();images=root/'image-evidence';images.mkdir()
    roles=['deployments','providers','data-acceptance','rollback','artifacts','releases','builders','first-cycle','images','policy']
    private={}
    for role in roles:
        key=keys/(role+'.key');pub=(images if role in {'images','policy'} else trust)/(role+'-ed25519.pub')
        subprocess.run(['/usr/bin/openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,capture_output=True)
        subprocess.run(['/usr/bin/openssl','pkey','-in',str(key),'-pubout','-out',str(pub)],check=True,capture_output=True)
        private[role]=key
    token,stage,controller,policy=model(int(time.time()))
    token['deployment_receipt_sha256']=receipt;stage['receipt_sha256']=receipt
    policy.update(schema='hope.image-policy.v1',issued_at=int(time.time())-1,revoked_images=[])
    token.update(controller_process_sha256=a.process_observation()['process_sha256'],controller_argv_sha256=a.process_observation()['argv_sha256'])
    def sign(path,value,key):
        path.write_bytes(r._canonical(value))
        subprocess.run(['/usr/bin/openssl','pkeyutl','-sign','-rawin','-inkey',str(key),'-in',str(path),
                        '-out',str(path.with_suffix('.sig'))],check=True,capture_output=True)
    def policy_pin():sign(images/'policy.json',policy,private['policy'])
    authorizations=root/'first-cycle-authorizations';authorizations.mkdir()
    def pin():
        value=r._digest(r._canonical(token));path=authorizations/f'{value}.json'
        sign(path,token,private['first-cycle'])
        (authorizations/f"{token['request_sha256']}.ref").write_text(value,encoding='ascii')
        return value
    def stage_probe(*args,**kw):
        return dict(deepcopy(stage),expires_at=min(int(time.time())+5,token['expires_at']))
    probes=dict(stage_probe=stage_probe,runtime_probe=Mock(side_effect=lambda *args:deepcopy(controller)))
    policy_pin();pin()
    def verify():return a.verify(root,SHA,token['request_sha256'],receipt,FACTORY,**probes)
    return dict(root=root,token=token,stage=stage,controller=controller,policy=policy,pin=pin,policy_pin=policy_pin,
                private=private,verify=verify,probes=probes)
