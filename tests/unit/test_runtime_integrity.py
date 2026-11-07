"""Independent approved manifests, real files and adversarial replacements."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest
from test_package_provenance import signed_runtime

from hope.api import runtime_integrity as m

SHA="2"*40


@pytest.fixture(params=["shared","release"])
def runtime(tmp_path,monkeypatch,request):
    if os.name!="posix":pytest.skip("Native ownership/no-follow/runtime storage; exercised on Linux CI")
    monkeypatch.setattr(m,"OWNER",os.geteuid());m._CACHE.clear()
    root=tmp_path/"deployment";root.mkdir();release=root/"releases"/SHA
    (release/"constraints").mkdir(parents=True)
    (release/"constraints/ci-test.txt").write_text("\n".join(name+"==1.0" for name in sorted(m.REQUIRED))+"\n")
    mode=request.param;environment=m.environment_path(root,SHA,mode)
    (environment/"bin").mkdir(parents=True)
    (environment/"bin/python").write_bytes(b"trusted interpreter");(environment/"bin/python").chmod(0o755)
    (environment/"pyvenv.cfg").write_text("version = 3.13.1\ninclude-system-site-packages = false\n")
    site=environment/"lib/python3.13/site-packages";site.mkdir(parents=True)
    for name in sorted(m.REQUIRED):
        info=site/(name+"-1.0.dist-info");info.mkdir()
        (info/"METADATA").write_text(f"Name: {name}\nVersion: 1.0\n\nReviewed test package\n")
    package=site/"sqlalchemy";package.mkdir();(package/"__init__.py").write_text("reviewed = True\n")
    (root/"runtime-manifests").mkdir()
    candidate=m.build_runtime_manifest(root,SHA,mode)
    path=m.manifest_path(root,SHA,mode);path.write_bytes(m._canonical(candidate))
    digest=hashlib.sha256(m._canonical(candidate)).hexdigest()
    return root,environment,mode,candidate,path,digest


def test_approved_manifest_authenticates_content_and_pinned_dependencies_without_outputting_paths(runtime):
    root,env,mode,candidate,path,digest=runtime
    result=m.attest_runtime(root,SHA,mode,expected_manifest=digest)
    assert result["state"]=="VERIFIED" and result["manifest_sha256"]==digest
    assert result["execution_authorized"] is False and result["file_count"]==len(candidate["files"])
    assert str(root) not in json.dumps(result) and "site-packages" not in json.dumps(result)
    assert m.attest_runtime(root,SHA,mode,expected_manifest=digest)==result  # verified immutable cache


@pytest.mark.parametrize("changed",["package","metadata","configuration","system_site","missing_dependency","missing_python","manifest","identity","constraints","import_hook","external_path"])
def test_runtime_or_authority_tampering_never_reuses_a_verified_cache(runtime,changed):
    root,env,mode,candidate,path,digest=runtime
    m.attest_runtime(root,SHA,mode,expected_manifest=digest)
    site=env/"lib/python3.13/site-packages"
    if changed=="package":(site/"sqlalchemy/__init__.py").write_text("substituted = True\n")
    elif changed=="metadata":(site/"sqlalchemy-1.0.dist-info/METADATA").write_text("Name: sqlalchemy\nVersion: 2.0\n")
    elif changed=="configuration":(env/"pyvenv.cfg").write_text("version = 3.12.0\ninclude-system-site-packages = false\n")
    elif changed=="system_site":(env/"pyvenv.cfg").write_text("version = 3.13.1\ninclude-system-site-packages = true\n")
    elif changed=="missing_dependency":(site/"sqlalchemy-1.0.dist-info/METADATA").unlink()
    elif changed=="missing_python":(env/"bin/python").unlink()
    elif changed=="manifest":path.write_text("{broken secret")
    elif changed=="identity":
        value=deepcopy(candidate);value["release_commit"]="3"*40;path.write_bytes(m._canonical(value))
    elif changed=="constraints":(root/"releases"/SHA/"constraints/ci-test.txt").write_text("# changed\n"+"\n".join(name+"==1.0" for name in sorted(m.REQUIRED)))
    elif changed=="import_hook":(site/"unreviewed.pth").write_text("import untrusted_payload\n")
    else:(site/"escape.pth").write_text("/tmp\n")
    result=m.diagnostic(root,SHA,mode,expected_manifest=digest)
    assert result["state"]=="MISMATCH" and result["execution_authorized"] is False
    assert "secret" not in json.dumps(result)


def test_manifest_in_inspected_environment_is_not_an_authority(runtime):
    root,env,mode,candidate,path,digest=runtime
    path.unlink();(env/"approved-manifest.json").write_bytes(m._canonical(candidate))
    assert m.diagnostic(root,SHA,mode)["state"]=="UNVERIFIED"


@pytest.mark.parametrize("scope",["manifest","manifest_directory","environment","package","interpreter"])
def test_writable_or_linked_runtime_storage_is_rejected(runtime,scope):
    root,env,mode,candidate,path,digest=runtime
    target={"manifest":path,"manifest_directory":path.parent,"environment":env,
        "package":env/"lib/python3.13/site-packages/sqlalchemy/__init__.py","interpreter":env/"bin/python"}[scope]
    target.chmod(0o777 if target.is_dir() else 0o666)
    assert m.diagnostic(root,SHA,mode,expected_manifest=digest)["state"]=="MISMATCH"


@pytest.mark.parametrize("scope",["manifest","environment","bin","package"])
def test_symlink_substitution_cannot_authenticate_an_alternative_tree(runtime,scope):
    root,env,mode,candidate,path,digest=runtime
    target={"manifest":path,"environment":env,"bin":env/"bin",
        "package":env/"lib/python3.13/site-packages/sqlalchemy/__init__.py"}[scope]
    retained=target.with_name(target.name+"-retained");target.rename(retained)
    target.symlink_to(retained,target_is_directory=retained.is_dir())
    assert m.diagnostic(root,SHA,mode,expected_manifest=digest)["state"]=="MISMATCH"


@pytest.mark.parametrize("scope",["package","interpreter","manifest","constraints"])
def test_same_content_replacement_during_attestation_invalidates_the_window(runtime,monkeypatch,scope):
    root,env,mode,candidate,path,digest=runtime
    target={"package":env/"lib/python3.13/site-packages/sqlalchemy/__init__.py","interpreter":env/"bin/python",
        "manifest":path,"constraints":root/"releases"/SHA/"constraints/ci-test.txt"}[scope]
    read=m._read;fingerprint=m._fingerprint;replaced=False
    def replace_after(file):
        nonlocal replaced
        # Replace after the first complete inventory but before its closing
        # metadata proof, including a same-byte manifest/constraint exchange.
        trigger=env/"pyvenv.cfg" if scope in {"manifest","constraints"} else target
        if file==trigger and not replaced:
            replaced=True;retained=target.with_name(target.name+"-old");target.rename(retained)
            target.write_bytes(retained.read_bytes());target.chmod(retained.stat().st_mode&0o777)
    def swap(file,*a,**k):
        result=read(file,*a,**k);replace_after(file)
        return result
    def hash_swap(file,*a,**k):
        result=fingerprint(file,*a,**k);replace_after(file);return result
    monkeypatch.setattr(m,"_read",swap)
    monkeypatch.setattr(m,"_fingerprint",hash_swap)
    with pytest.raises((OSError,RuntimeError)):m.attest_runtime(root,SHA,mode,expected_manifest=digest)
    assert replaced


def test_replacing_package_then_restoring_mtime_cannot_hide_content_change(runtime):
    root,env,mode,candidate,path,digest=runtime
    m.attest_runtime(root,SHA,mode,expected_manifest=digest)
    package=env/"lib/python3.13/site-packages/sqlalchemy/__init__.py";before=package.stat()
    package.write_bytes(b"x"*before.st_size);os.utime(package,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert m.diagnostic(root,SHA,mode,expected_manifest=digest)["state"]=="MISMATCH"


def test_entry_byte_and_time_limits_fail_closed(runtime,monkeypatch):
    root,env,mode,candidate,path,digest=runtime
    monkeypatch.setattr(m,"MAX_FILES",1)
    with pytest.raises(RuntimeError,match="RUNTIME_ENTRY_LIMIT"):m.attest_runtime(root,SHA,mode)
    monkeypatch.setattr(m,"MAX_FILES",20000);monkeypatch.setattr(m,"MAX_BYTES",1)
    with pytest.raises(RuntimeError,match="RUNTIME_BYTE_LIMIT"):m.attest_runtime(root,SHA,mode)
    monkeypatch.setattr(m,"MAX_BYTES",512*1024*1024)
    with pytest.raises(RuntimeError,match="RUNTIME_DEADLINE"):m.attest_runtime(root,SHA,mode,timeout=0)


def test_legitimate_interpreter_and_internal_library_links_are_authenticated(runtime):
    root,env,mode,candidate,path,digest=runtime
    system=root/"system/bin/python3.13";system.parent.mkdir(parents=True)
    (env/"bin/python").rename(system)
    (env/"bin/python").symlink_to(system)
    (env/"bin/python3").symlink_to(env/"bin/python")
    (env/"lib64").symlink_to(env/"lib",target_is_directory=True)
    candidate=m.build_runtime_manifest(root,SHA,mode);path.write_bytes(m._canonical(candidate))
    result=m.attest_runtime(root,SHA,mode)
    assert result["state"]=="VERIFIED" and candidate["links"]["lib64"]=="@runtime/lib"


def test_rollback_runtime_must_match_its_journal_pinned_manifest(signed_runtime,monkeypatch):
    from hope.api import dashboard_activation as activation
    from unittest.mock import Mock
    root,env,mode,candidate,path=signed_runtime;digest=m._digest(m._canonical(candidate))
    monkeypatch.setattr(activation,"OWNER",os.geteuid())
    layout=activation.Layout(root=root);host=activation.Host(layout)
    record=dict(schema="hope.dashboard-activation.v3",target="3"*40,shared="1"*40,previous=SHA,
        phase="ROLLBACK_FAILED",backup_id="a"*32,timer_active=False,pid=123,
        boot="1"*36,ticks="10",runtime="release",previous_runtime=mode,
        runtime_manifest="b"*64,previous_runtime_manifest=digest)
    activation._save(layout.journal,record)
    host.paper_safe=Mock();host.release=Mock(return_value=root/"releases"/SHA);host.database=Mock()
    host.run=Mock(return_value="DASHBOARD_RUNTIME_VERIFIED\n")
    host.rollback_preflight(SHA)
    assert host.run.call_count==1
    record["previous_runtime_manifest"]="c"*64;activation._save(layout.journal,record);host.run.reset_mock()
    with pytest.raises(RuntimeError,match="RUNTIME_MANIFEST_CHANGED"):host.rollback_preflight(SHA)
    host.run.assert_not_called()


@pytest.mark.parametrize("changed",[{"state":"FAKE"},{"private_path":"/secret"},
    {"execution_authorized":True},{"file_count":True},{"release_commit":"wrong"},
    {"manifest_sha256":"wrong"},{"runtime_mode":"automatic"},{"code":"private error"}])
def test_safe_runtime_projection_rejects_untrusted_or_contradictory_evidence(changed):
    value=dict(schema="hope.runtime-integrity.v1",state="VERIFIED",code="MANIFEST_AND_RUNTIME_MATCH",
        release_commit=SHA,runtime_mode="shared",manifest_sha256="a"*64,inventory_sha256="c"*64,process_sha256=None,file_count=1,execution_authorized=False)
    value.update(changed)
    with pytest.raises(RuntimeError,match="RUNTIME_"):m.safe_evidence(value)


def test_absent_and_legacy_evidence_are_bounded_and_never_verified():
    for value in (None,m.unverified(),m.unverified("ACTIVATION_BLOCKED",blocked=True)):
        result=m.safe_evidence(value)
        assert result["state"]!="VERIFIED" and result["execution_authorized"] is False


@pytest.mark.parametrize("cache",["__pycache__/module.cpython-313.pyc","module.pyc","module.pyo"])
def test_ignored_executable_source_cache_is_rejected(runtime,cache):
    root,*_=runtime;release=root/"releases"/SHA
    source=release/"src/hope/api";source.mkdir(parents=True)
    (source/"module.py").write_text("reviewed = True\n")
    before=m.source_snapshot(release)
    assert m.source_snapshot(release)==before
    target=source/cache;target.parent.mkdir(exist_ok=True);target.write_bytes(b"unreviewed bytecode")
    with pytest.raises(RuntimeError,match="SOURCE_CACHE_DENIED"):m.source_snapshot(release)


@pytest.mark.parametrize("release_changes",[False,True])
def test_cli_is_read_only_revalidates_release_and_sanitizes_failures(runtime,monkeypatch,capsys,release_changes):
    from contextlib import contextmanager
    from unittest.mock import Mock
    from hope.api import operations_deployment as operations
    root,env,mode,candidate,path,digest=runtime
    actual_path=Path
    monkeypatch.setattr(m,"Path",lambda *args:root if args==("/opt/hope-operations",) else actual_path(*args))
    @contextmanager
    def guard(observed,*,shared):
        assert observed==root and shared is True
        yield
    monkeypatch.setattr(operations,"_activation_guard",guard)
    inspector=Mock(side_effect=[{"status":"COMPATIBLE_CODE"},RuntimeError("private secret")]
        if release_changes else None)
    monkeypatch.setattr(operations,"inspect_release",inspector)
    before={str(item):item.stat().st_size for item in root.rglob("*") if item.is_file()}
    assert m.main([SHA,"--runtime",mode])==(2 if release_changes else 0)
    output=capsys.readouterr().out;result=json.loads(output)
    assert result["state"]==("BLOCKED" if release_changes else "VERIFIED")
    assert len(output)<1024 and "secret" not in output and str(root) not in output
    assert inspector.call_count==2
    assert before=={str(item):item.stat().st_size for item in root.rglob("*") if item.is_file()}
