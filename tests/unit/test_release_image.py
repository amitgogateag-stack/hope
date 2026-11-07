"""OCI content addresses, independent ephemeral authorities and native rootfs execution."""
from copy import deepcopy
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

import pytest
from hope.api import release_image as image, release_builder as builder, runtime_integrity as r
from hope.api.image_diagnostics import image_observation
from test_release_builder import trusted_python, build_case
from build_approval_fixture import sign


def write_signed(path,value,key):
    path.write_bytes(r._canonical(value));sign(path,key)


def authority(root,name):
    private=root/(name+'.key');public=root/(name+'.pub')
    subprocess.run(['/usr/bin/openssl','genpkey','-algorithm','ED25519','-out',str(private)],check=True,capture_output=True)
    subprocess.run(['/usr/bin/openssl','pkey','-in',str(private),'-pubout','-out',str(public)],check=True,capture_output=True)
    return private,public


@pytest.fixture
def image_case(build_case):
    args,plan,private,_=build_case
    # Explicit signed image-compatible OS prefix; v1 physical-prefix behavior
    # remains unchanged for existing host releases.
    plan.update(schema='hope.build-input.v2',os_python='/usr/bin/python3.13',deployment_root='/opt/hope-operations')
    write_signed(args['inputs'],plan,private);args['expected_inputs_digest']=r._digest(args['inputs'].read_bytes())
    release=builder.build_release(**args)
    folder=args['inputs'].parent
    release_private,release_public=authority(folder,'release-authority')
    base_private,base_public=authority(folder,'base-authority')
    release.update(schema='hope.release-approval.v1',state='APPROVED',approved=True)
    release_document=folder/'release.json';write_signed(release_document,release,release_private)
    python=args['python'];python_root=python.parent.parent
    entries={'usr/bin/python3.13':(python.read_bytes(),0o755,False),
        'etc/os-release':(Path('/etc/os-release').resolve().read_bytes(),0o644,False)}
    # A byte-matched disposable OS fixture, not a production base authority.
    # Include stdlib and actual ELF dependencies; no ambient credentials/config.
    binaries=[python]
    for path in sorted((python_root/'lib').rglob('*')):
        if not path.is_file():continue
        name='usr/lib/'+path.relative_to(python_root/'lib').as_posix()
        entries[name]=(path.read_bytes(),0o644,False)
        if path.read_bytes()[:4]==b'\x7fELF':binaries.append(path)
    for binary in binaries:
        output=subprocess.run(['/usr/bin/ldd',str(binary)],check=True,capture_output=True,text=True).stdout
        for line in output.splitlines():
            words=line.split();paths=[v for v in words if v.startswith('/')]
            for name in paths:
                path=Path(name)
                # libpython copied under /usr/lib is already resolved by RPATH.
                if path.is_relative_to(python_root):continue
                entries[path.as_posix().lstrip('/')]=(path.read_bytes(),0o755,False)
    base_bytes=image._tar(entries,plan['epoch']);base_archive=folder/'base.tar';base_archive.write_bytes(base_bytes)
    inventory={name:dict(sha256=r._digest(data),mode=mode,directory=directory) for name,(data,mode,directory) in sorted(entries.items())}
    base=dict(schema='hope.image-base.v1',layer_sha256=r._digest(base_bytes),
        os_release_sha256=r._digest(entries['etc/os-release'][0]),python_path='/usr/bin/python3.13',
        python_sha256=r._digest(python.read_bytes()),architecture='amd64',inventory_sha256=r._digest(r._canonical(inventory)))
    base_document=folder/'base.json';write_signed(base_document,base,base_private)
    work=args['work'].parent/'image-work';work.mkdir();(work/'.hope-image-builder').write_bytes(image.MARKER)
    arguments=dict(work=work,base_document=base_document,base_archive=base_archive,release_document=release_document,
        release_archive=args['work']/'a/release.tar',base_public=base_public,release_public=release_public)
    return arguments,plan,folder


@pytest.fixture
def approved_image(image_case):
    args,plan,folder=image_case;candidate=image.build_image(**args)
    key,public=authority(folder,'image-authority');policy_key,policy_public=authority(folder,'policy-authority')
    now=int(time.time())
    approval=dict(candidate,schema='hope.image-approval.v1',state='APPROVED',issued_at=now-1,expires_at=now+600)
    path=folder/'image.json';write_signed(path,approval,key)
    policy=dict(schema='hope.image-policy.v1',generation=7,issued_at=now-1,expires_at=now+300,revoked_images=[],revoked_keys=[])
    policy_path=folder/'policy.json';write_signed(policy_path,policy,policy_key)
    verification=dict(layout=args['work']/'a',approval_path=path,image_public=public,policy_path=policy_path,
        policy_public=policy_public,expected_sha=plan['release_commit'],now=now,minimum_generation=7)
    return args,candidate,verification,approval,key,policy,policy_key


def test_independent_signatures_reproducibility_full_linkage_and_rootfs_python(approved_image):
    args,candidate,verify,_,_,_,_=approved_image
    proof=image.verify_image(**verify)
    assert proof['state']=='VERIFIED' and proof['scope']=='DISPOSABLE_LAB_ONLY' and proof['execution_authorized'] is False
    assert proof['expires_at']==verify['now']+300
    for path in (args['work']/'a').rglob('*'):
        if path.is_file():assert path.read_bytes()==(args['work']/'b'/path.relative_to(args['work']/'a')).read_bytes()
    index=r.strict_json((verify['layout']/'index.json').read_bytes())
    manifest=r.strict_json((verify['layout']/'blobs/sha256'/index['manifests'][0]['digest'][7:]).read_bytes())
    root=args['work']/'rootfs';root.mkdir(mode=0o755)
    for layer in manifest['layers']:
        entries=image._members((verify['layout']/'blobs/sha256'/layer['digest'][7:]).read_bytes())
        for name,(payload,mode,directory) in entries.items():
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
            if directory:target.mkdir(exist_ok=True)
            else:target.write_bytes(payload)
            target.chmod(mode)
    # Real image interpreter + image-specific venv, non-root, isolated network.
    # No service, mount, host configuration or AWS operation is performed.
    python='/opt/hope-operations/release-venvs/'+candidate['release_commit']+'/bin/python'
    code='import sys,sqlalchemy;assert sys.version_info[:2]==(3,13);assert sqlalchemy.approved;assert sys.base_prefix=="/usr";print("IMAGE_PYTHON_VERIFIED")'
    result=subprocess.run(['sudo','-n','unshare','--net','chroot','--userspec=65532:65532',str(root),python,'-I','-B','-c',code],
        env={'PATH':'/usr/bin:/bin','LANG':'C'},check=True,capture_output=True,text=True,timeout=15)
    assert result.stdout.strip()=='IMAGE_PYTHON_VERIFIED'
    assert not (root/'root').exists() and not (root/'opt/hope-operations/runtime-trust').exists()


@pytest.mark.parametrize('fault',['image_revoked','key_revoked','expired_policy','future_policy','expired_approval','future_approval',
    'generation','unsigned','wrong_signer','release','blob','config','sbom','runtime','source','archive_link','base_inventory','extra_overlay'])
def test_revocation_expiry_substitution_and_contradictions_fail_closed(approved_image,fault):
    args,candidate,verify,approval,key,policy,policy_key=approved_image
    if fault=='image_revoked':policy['revoked_images']=[candidate['image_sha256']]
    elif fault=='key_revoked':policy['revoked_keys']=[image._key_digest(verify['image_public'])]
    elif fault=='expired_policy':policy['expires_at']=verify['now']
    elif fault=='future_policy':policy['issued_at']=verify['now']+1
    elif fault=='expired_approval':approval['expires_at']=verify['now']
    elif fault=='future_approval':approval['issued_at']=verify['now']+1
    elif fault=='generation':verify['minimum_generation']=8
    elif fault=='release':verify['expected_sha']='f'*40
    elif fault=='base_inventory':approval['base_inventory_sha256']='f'*64
    elif fault=='archive_link':approval['hashes']['release.tar']='f'*64
    else:
        if fault in {'unsigned','wrong_signer'}:
            write_signed(verify['approval_path'],approval,policy_key if fault=='wrong_signer' else key)
            if fault=='unsigned':verify['approval_path'].with_suffix('.sig').unlink()
            with pytest.raises(Exception):image.verify_image(**verify)
            return
        # Recompute content addresses AND independently sign contradictory images.
        # A valid signature cannot bypass internal manifest/SBOM/source linkage.
        layout=verify['layout'];index=r.strict_json((layout/'index.json').read_bytes())
        manifest=r.strict_json((layout/'blobs/sha256'/index['manifests'][0]['digest'][7:]).read_bytes())
        layers=[(layout/'blobs/sha256'/v['digest'][7:]).read_bytes() for v in manifest['layers']]
        if fault=='blob':
            (layout/'blobs/sha256'/manifest['layers'][0]['digest'][7:]).write_bytes(b'corrupt')
        else:
            entries=image._members(layers[1]);prefix='opt/hope-operations/'
            if fault=='extra_overlay':entries['tmp/unapproved']=(b'unapproved',0o644,False)
            elif fault in {'sbom','runtime'}:
                name=prefix+'candidate-evidence/'+candidate['release_commit']+'/'+('sbom.json' if fault=='sbom' else 'runtime-manifest.json')
                entries[name]=(b'{}',0o644,False)
            elif fault=='source':
                name=next(n for n in entries if '/releases/' in n);entries[name]=(b'changed',0o644,False)
            layers[1]=image._tar(entries,1735689600)
            revised,digest=image._layout(layers[0],layers[1],'amd64',0,candidate['release_commit'])
            if fault=='config':
                idx=r.strict_json(revised['index.json']);m=r.strict_json(revised['blobs/sha256/'+idx['manifests'][0]['digest'][7:]])
                c=r.strict_json(revised['blobs/sha256/'+m['config']['digest'][7:]]);c['config']['Entrypoint']=['/bin/sh']
                payload=r._canonical(c);revised['blobs/sha256/'+r._digest(payload)]=payload;m['config']=image._descriptor(image.CONFIG,payload)
                payload=r._canonical(m);digest=r._digest(payload);revised['blobs/sha256/'+digest]=payload
                revised['index.json']=r._canonical(dict(schemaVersion=2,manifests=[image._descriptor(image.MANIFEST,payload)]))
            for name,payload in revised.items():(layout/name).write_bytes(payload)
            approval['image_sha256']=digest
    write_signed(verify['policy_path'],policy,policy_key);write_signed(verify['approval_path'],approval,key)
    with pytest.raises(Exception):image.verify_image(**verify)


@pytest.mark.parametrize('name,type_,mode,link',[('../escape',tarfile.REGTYPE,0o644,''),('/absolute',tarfile.REGTYPE,0o644,''),
    ('a',tarfile.SYMTYPE,0o755,'/etc/shadow'),('a',tarfile.LNKTYPE,0o644,'b'),('a',tarfile.FIFOTYPE,0o644,''),
    ('a',tarfile.REGTYPE,0o4755,''),('.wh.secret',tarfile.REGTYPE,0o644,''),('a\\b',tarfile.REGTYPE,0o644,'')])
def test_unsafe_layer_entries_rejected_before_extraction(name,type_,mode,link):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        member=tarfile.TarInfo(name);member.type=type_;member.mode=mode;member.linkname=link;archive.addfile(member)
    with pytest.raises(RuntimeError):image._members(stream.getvalue())


def test_unsigned_or_private_diagnostics_never_expose_paths(tmp_path):
    result=image_observation(tmp_path,'a'*40)
    assert result['state']=='UNVERIFIED' and result['execution_authorized'] is False
    assert str(tmp_path) not in str(result) and len(result)==8


@pytest.mark.parametrize('fault',['missing_marker','base_signature','release_signature','base_bytes','release_bytes','base_python','unapproved_prefix'])
def test_builder_rejects_unapproved_inputs_before_writing_layout(image_case,fault):
    args,_,folder=image_case
    if fault=='missing_marker':(args['work']/'.hope-image-builder').unlink()
    elif fault in {'base_signature','release_signature'}:args['base_document' if fault.startswith('base') else 'release_document'].with_suffix('.sig').write_bytes(b'x'*64)
    elif fault in {'base_bytes','release_bytes'}:args['base_archive' if fault.startswith('base') else 'release_archive'].write_bytes(b'x')
    elif fault=='base_python':
        document=r.strict_json(args['base_document'].read_bytes());document['python_sha256']='a'*64
        write_signed(args['base_document'],document,folder/'base-authority.key')
    else:
        document=r.strict_json(args['release_document'].read_bytes());document['deployment_root']='/unapproved'
        write_signed(args['release_document'],document,folder/'release-authority.key')
    with pytest.raises(Exception):image.build_image(**args)
    assert not (args['work']/'a').exists() and not (args['work']/'candidate.json').exists()
