"""Existing independent build chain, with ephemeral test authority only.

No production key, acceptance, grant, session or financial authority is created.
"""
from copy import deepcopy
import hashlib
from unittest.mock import Mock

import pytest

from hope.api import dashboard_activation as activation,runtime_integrity as runtime,release_builder
from test_package_provenance import signed_runtime,SHA
from test_dashboard_only_activation import setup,OLD,NEW
from test_activation_guard import guard_platform
from build_approval_fixture import approve_fixture_build,sign


MARKET='docs/dashboard/market.html'
PREVIEW='docs/dashboard/preview.html'


def approved_market_fixture(case):
    root,_,mode,manifest,_=case
    previous=dict(manifest,schema='hope.runtime-manifest.v2');previous.pop('release_build_sha256')
    files={PREVIEW:b'<h1>HOPE</h1>\n',MARKET:b'<h1>Private observations</h1>\n','README.md':b'fixture source\n'}
    private=root.parent/'fixture-only.key'
    approved=approve_fixture_build(root,SHA,mode,previous,private,source_files=files)
    runtime.manifest_path(root,SHA,mode).write_bytes(runtime._canonical(approved))
    return root,mode,approved,files,private


def test_market_digest_comes_from_approved_archive_not_local_file(signed_runtime):
    root,mode,approved,files,_=approved_market_fixture(signed_runtime)
    local=root/'releases'/SHA/MARKET;local.parent.mkdir(parents=True);local.write_bytes(b'untrusted local replacement')
    result=runtime.attest_runtime(root,SHA,mode,expected_manifest=runtime._digest(runtime._canonical(approved)),
        release_source=PREVIEW,include_market_asset=True)
    assert result['market_source_sha256']==hashlib.sha256(files[MARKET]).hexdigest()
    assert result['market_source_sha256']!=hashlib.sha256(local.read_bytes()).hexdigest()
    assert result['release_source_sha256']==hashlib.sha256(files[PREVIEW]).hexdigest()
    assert result['execution_authorized'] is False and result['build_state']=='VERIFIED'


def test_old_approved_archive_explicitly_omits_market_without_losing_preview(signed_runtime):
    root,_,mode,_,_=signed_runtime
    result=runtime.attest_runtime(root,SHA,mode,release_source=PREVIEW,include_market_asset=True)
    assert result['market_source_sha256'] is None
    assert result['release_source_sha256']==hashlib.sha256(b'<h1>HOPE</h1>\n').hexdigest()
    assert result['execution_authorized'] is False


def test_missing_build_approval_cannot_supply_optional_market_hash(signed_runtime):
    root,_,mode,manifest,_=signed_runtime
    previous=dict(manifest,schema='hope.runtime-manifest.v2');previous.pop('release_build_sha256')
    runtime.manifest_path(root,SHA,mode).write_bytes(runtime._canonical(previous))
    assert runtime.attest_runtime(root,SHA,mode)['build_state']=='UNVERIFIED'
    with pytest.raises(RuntimeError,match='BUILD_SOURCE_UNVERIFIED'):
        runtime.attest_runtime(root,SHA,mode,include_market_asset=True)


@pytest.mark.parametrize('fault',['missing_signature','modified_archive','signed_contradiction'])
def test_market_selection_never_bypasses_full_approved_archive_chain(signed_runtime,fault):
    root,mode,approved,_,private=approved_market_fixture(signed_runtime)
    assert runtime.attest_runtime(root,SHA,mode,include_market_asset=True)['market_source_sha256']
    folder=root/'release-builds';prefix=SHA+'-'+mode;approval_path=folder/(prefix+'.json');archive=folder/(prefix+'.tar')
    if fault=='missing_signature':approval_path.with_suffix('.sig').unlink()
    elif fault=='modified_archive':archive.write_bytes(b'not approved')
    else:
        from hope.api.release_image import _members
        entries={name:(data,permissions) for name,(data,permissions,_) in _members(archive.read_bytes()).items()}
        entries['release/'+MARKET]=(b'contradiction inside separately signed archive',0o644)
        approval=runtime.strict_json(approval_path.read_bytes())
        release_builder._tar(archive,entries,approval['epoch'])
        approval['hashes']['release.tar']=runtime._digest(archive.read_bytes())
        approval_path.write_bytes(runtime._canonical(approval));sign(approval_path,private)
        approved=dict(approved,release_build_sha256=runtime._digest(approval_path.read_bytes()))
        runtime.manifest_path(root,SHA,mode).write_bytes(runtime._canonical(approved))
    with pytest.raises(RuntimeError,match='RELEASE_BUILD_UNVERIFIED'):
        runtime.attest_runtime(root,SHA,mode,release_source=PREVIEW,include_market_asset=True)


@pytest.mark.parametrize('flag',[None,1,'true',[]])
def test_asset_selector_is_bounded_type_and_is_not_approval(tmp_path,flag):
    with pytest.raises(RuntimeError,match='BUILD_SOURCE_UNVERIFIED'):
        runtime.attest_runtime(tmp_path,SHA,'release',include_market_asset=flag)


def activated_case(setup):
    layout,host=setup;host.runtime_manifests={OLD:'a'*64,NEW:'b'*64}
    activation.activate(layout,host,target=NEW,shared=OLD,ci_run=123,ci_attempt=1,runtime='release')
    host.calls.clear();return layout,host


@pytest.mark.parametrize('digest',[None,'e'*64])
def test_gate_hands_off_only_selected_build_digest_and_preserves_old_archives(setup,monkeypatch,digest):
    layout,host=activated_case(setup);attest=activation.attest_runtime
    def checked(*args,**kwargs):
        assert kwargs['include_market_asset'] is True and kwargs['release_source']==PREVIEW
        result=attest(*args,**kwargs);result['market_source_sha256']=digest;return result
    spy=Mock(side_effect=checked);monkeypatch.setattr(activation,'attest_runtime',spy)
    result=activation.runtime_gate(layout,NEW)
    spy.assert_called_once_with(layout.root,NEW,'release',expected_manifest='b'*64,
        release_source=PREVIEW,include_market_asset=True)
    assert result['market_preview_sha256']==digest and result['preview_sha256']=='d'*64
    assert result['execution_authorized'] is False and host.calls==[]
    assert 'market_source_sha256' not in result['runtime_integrity']


@pytest.mark.parametrize('digest',[True,'bad','E'*64,['e'*64],{}])
def test_gate_rejects_malformed_positive_asset_claim_before_serving(setup,monkeypatch,digest):
    layout,host=activated_case(setup);attest=activation.attest_runtime;before=layout.journal.read_bytes()
    def bad(*args,**kwargs):
        result=attest(*args,**kwargs);result['market_source_sha256']=deepcopy(digest);return result
    monkeypatch.setattr(activation,'attest_runtime',bad)
    with pytest.raises(RuntimeError,match='BUILD_SOURCE_UNVERIFIED'):activation.runtime_gate(layout,NEW)
    assert layout.journal.read_bytes()==before and host.calls==[]
