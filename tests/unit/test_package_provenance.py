"""Actual wheel bytes and Ed25519 signatures; no inspected package imports."""
import base64
from copy import deepcopy
import csv
import io
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

from hope.api import package_provenance as p,runtime_integrity as r

SHA="2"*40


def wheel(name="demo",version="1.0",*,requires=(),tag="py3-none-any",extra=None):
    directory=f"{name}-{version}.dist-info"
    files={name.replace("-","_")+"/__init__.py":b"approved = True\n",
        directory+"/METADATA":("Metadata-Version: 2.1\nName: "+name+"\nVersion: "+version+"\n"+
            "".join("Requires-Dist: "+value+"\n" for value in requires)+"\n").encode(),
        directory+"/WHEEL":("Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: "+tag+"\n").encode()}
    files.update(extra or {})
    stream=io.StringIO();writer=csv.writer(stream,lineterminator="\n")
    for path,value in files.items():writer.writerow((path,"sha256="+base64.urlsafe_b64encode(bytes.fromhex(r._digest(value))).decode().rstrip("="),len(value)))
    writer.writerow((directory+"/RECORD","",""));files[directory+"/RECORD"]=stream.getvalue().encode()
    result=io.BytesIO()
    with zipfile.ZipFile(result,"w",zipfile.ZIP_DEFLATED) as archive:
        for path,value in files.items():archive.writestr(path,value)
    return result.getvalue(),files


@pytest.fixture
def package_case(tmp_path):
    payload,files=wheel();site="lib/python3.13/site-packages"
    manifest=dict(files={site+"/"+k:r._digest(v) for k,v in files.items()},distributions={"demo":"1.0"},interpreter={"sha256":"a"*64})
    return tmp_path,{"demo-1.0-py3-none-any.whl":payload},manifest


def test_candidate_has_no_approval_and_exact_relationships(package_case):
    root,wheels,manifest=package_case
    value=p.build_candidate(root,SHA,"shared",wheels,runtime_manifest=manifest)
    assert value["artifacts"][0]["sha256"]==r._digest(next(iter(wheels.values())))
    assert value["platform"]["python"]=="3.13" and value["artifacts"][0]["requires_dist"]==[]
    assert not {"approved","signature","private_key"}&value.keys()


@pytest.mark.parametrize("change",["bytes","unexpected_import","missing_package","extra_distribution","wrong_version"])
def test_unapproved_package_drift_is_not_a_candidate(package_case,change):
    root,wheels,manifest=package_case
    if change=="bytes":manifest["files"]["lib/python3.13/site-packages/demo/__init__.py"]="b"*64
    elif change=="unexpected_import":manifest["files"]["lib/python3.13/site-packages/injected.py"]="b"*64
    elif change=="missing_package":del manifest["files"]["lib/python3.13/site-packages/demo/__init__.py"]
    elif change=="extra_distribution":manifest["distributions"]["extra"]="1.0"
    else:manifest["distributions"]["demo"]="2.0"
    with pytest.raises(RuntimeError):p.build_candidate(root,SHA,"shared",wheels,runtime_manifest=manifest)


@pytest.mark.parametrize("path",["../escape.py","/absolute.py","bad\\path.py","a//b.py","a/./b.py","C:drive.py"])
def test_malicious_wheel_paths_are_rejected(path):
    payload,_=wheel(extra={path:b"bad"})
    with pytest.raises(RuntimeError,match="PATH_INVALID"):p.inspect_wheel(payload)


@pytest.mark.parametrize("requirement,accepted",[("missing>=1",False),("missing; extra == 'test'",True),
    ("missing; python_version < '3.10'",True),("missing; python_version >= '3.13'",False),
    ("missing @ https://untrusted.example/a.whl",False),("missing; arbitrary()",False)])
def test_dependency_markers_and_missing_relationships_fail_closed(package_case,requirement,accepted):
    root,_,manifest=package_case;payload,files=wheel(requires=(requirement,))
    manifest["files"]={"lib/python3.13/site-packages/"+k:r._digest(v) for k,v in files.items()}
    if accepted:assert p.build_candidate(root,SHA,"shared",{"demo.whl":payload},runtime_manifest=manifest)
    else:
        with pytest.raises((RuntimeError,SyntaxError)):p.build_candidate(root,SHA,"shared",{"demo.whl":payload},runtime_manifest=manifest)


@pytest.mark.parametrize("tag",["cp312-cp312-manylinux_2_17_x86_64","py3-none-not_a_platform","cp313-cp313-manylinux_999_0_x86_64"])
def test_incompatible_wheels_are_not_approved(tag):
    with pytest.raises(RuntimeError,match="PLATFORM_MISMATCH"):p.inspect_wheel(wheel(tag=tag)[0])


@pytest.mark.parametrize("spec,accepted",[("==1.0",True),("!=1.0",False),(">=1.0",True),("<=1.0",True),
    (">1.0",False),("<1.1",True),("~=1.0",True),("~=1.0.1",False),(">=0.9,<2.0",True)])
def test_exact_dependency_version_relationships_are_enforced(spec,accepted):
    assert p._satisfies("1.0",spec) is accepted


@pytest.mark.parametrize("marker,expected",[("python_version == '3.13'",True),("python_version != '3.13'",False),
    ("python_version > '3.12'",True),("python_version <= '3.13'",True),("'3.13' in '3.12 3.13'",True),
    ("extra == 'binary' and python_version >= '3.13'",True),("extra == 'test' or python_version < '3.10'",False)])
def test_marker_semantics_use_target_python_and_explicit_extras(marker,expected):
    assert p._marker(marker,["binary"]) is expected


@pytest.mark.parametrize("marker",["__import__('os').system('bad')","unknown_variable == 'x'","['bad']","1 == 1","'x' + 'y'"])
def test_markers_cannot_execute_python_or_gain_unknown_authority(marker):
    with pytest.raises(RuntimeError):p._marker(marker,[])


@pytest.mark.parametrize("fault",["record_hash","record_missing","record_duplicate","symlink","zip_duplicate","encrypted","oversize"])
def test_wheel_container_attacks_fail_before_approval(fault,monkeypatch):
    payload,files=wheel();directory="demo-1.0.dist-info"
    if fault=="record_hash":files["demo/__init__.py"]=b"modified after record"
    elif fault=="record_missing":files.pop("demo/__init__.py")
    elif fault=="record_duplicate":files[directory+"/RECORD"]+=files[directory+"/RECORD"].splitlines(keepends=True)[0]
    elif fault=="oversize":monkeypatch.setattr(p,"MAX_EXPANDED",1)
    result=io.BytesIO()
    with zipfile.ZipFile(result,"w") as archive:
        for name,value in files.items():
            info=zipfile.ZipInfo(name)
            if fault=="symlink" and name=="demo/__init__.py":info.external_attr=0o120777<<16
            archive.writestr(info,value)
        if fault=="zip_duplicate":
            import warnings
            with warnings.catch_warnings():warnings.simplefilter("ignore");archive.writestr("demo/__init__.py",b"duplicate")
    if fault=="encrypted":
        # The ZIP encryption bit is rejected before any decryption/read path.
        damaged=bytearray(result.getvalue());position=damaged.index(b"PK\x01\x02");damaged[position+8]|=1;payload=bytes(damaged)
    else:payload=result.getvalue()
    with pytest.raises((RuntimeError,zipfile.BadZipFile)):p.inspect_wheel(payload)


@pytest.fixture(params=["shared","release"])
def signed_runtime(tmp_path,monkeypatch,request):
    if os.name!="posix":pytest.skip("Real POSIX trust and system OpenSSL verified in Linux CI")
    monkeypatch.setattr(r,"OWNER",os.geteuid());r._CACHE.clear()
    root=tmp_path/"deployment";root.mkdir();release=root/"releases"/SHA
    (release/"constraints").mkdir(parents=True);(release/"src").mkdir()
    (release/"constraints/ci-test.txt").write_text("\n".join(n+"==1.0" for n in sorted(r.REQUIRED)))
    mode=request.param;env=r.environment_path(root,SHA,mode);(env/"bin").mkdir(parents=True)
    (env/"bin/python").write_bytes(b"reviewed interpreter");(env/"bin/python").chmod(0o755)
    (env/"pyvenv.cfg").write_text("version = 3.13.1\ninclude-system-site-packages = false\n")
    wheels={}
    for name in sorted(r.REQUIRED):
        payload,files=wheel(name);filename=name+"-1.0-py3-none-any.whl";wheels[filename]=payload
        for relative,value in files.items():
            target=env/"lib/python3.13/site-packages"/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(value)
    for folder in ("runtime-manifests","artifact-provenance","runtime-trust","wheelhouse"):(root/folder).mkdir()
    manifest=r.build_runtime_manifest(root,SHA,mode)
    descriptor=p.build_candidate(root,SHA,mode,wheels,runtime_manifest=manifest)
    path=root/"artifact-provenance"/(SHA+"-"+mode+".json");path.write_bytes(r._canonical(descriptor))
    # Ephemeral fixture signing authority outside the inspected deployment.
    private=tmp_path/"fixture-only.key"
    subprocess.run([str(p.OPENSSL),"genpkey","-algorithm","ED25519","-out",str(private)],check=True,capture_output=True)
    public=root/"runtime-trust/artifacts-ed25519.pub"
    subprocess.run([str(p.OPENSSL),"pkey","-in",str(private),"-pubout","-out",str(public)],check=True,capture_output=True)
    subprocess.run([str(p.OPENSSL),"pkeyutl","-sign","-rawin","-inkey",str(private),"-in",str(path),"-out",str(path.with_suffix(".sig"))],check=True,capture_output=True)
    for filename,payload in wheels.items():(root/"wheelhouse"/filename).write_bytes(payload)
    manifest=r.build_runtime_manifest(root,SHA,mode,provenance_digest=r._digest(path.read_bytes()))
    from build_approval_fixture import approve_fixture_build
    manifest=approve_fixture_build(root,SHA,mode,manifest,private)
    r.manifest_path(root,SHA,mode).write_bytes(r._canonical(manifest))
    return root,env,mode,manifest,path


def test_real_ed25519_and_installed_bytes_are_required_independently(signed_runtime):
    root,env,mode,manifest,path=signed_runtime
    evidence=r.attest_runtime(root,SHA,mode)
    assert evidence["provenance_state"]=="VERIFIED" and evidence["provenance_sha256"]==r._digest(path.read_bytes())
    assert str(root) not in str(evidence)
    assert r.attest_runtime(root,SHA,mode)==evidence


@pytest.mark.parametrize("tamper",["signature","self_approval","key","artifact","interpreter","package","manifest","missing"])
def test_signed_authority_or_runtime_tampering_is_blocked(signed_runtime,tamper):
    root,env,mode,manifest,path=signed_runtime;r.attest_runtime(root,SHA,mode)
    if tamper=="signature":path.with_suffix(".sig").write_bytes(b"x"*64)
    elif tamper=="self_approval":
        descriptor=deepcopy(p.build_candidate(root,SHA,mode,{p.name:p.read_bytes() for p in (root/"wheelhouse").iterdir()},runtime_manifest=manifest))
        descriptor["platform"]["machine"]="self-approved";path.write_bytes(r._canonical(descriptor))
        manifest["package_provenance_sha256"]=r._digest(path.read_bytes());r.manifest_path(root,SHA,mode).write_bytes(r._canonical(manifest))
    elif tamper=="key":(root/"runtime-trust/artifacts-ed25519.pub").write_bytes(b"fake key")
    elif tamper=="artifact":next((root/"wheelhouse").iterdir()).write_bytes(b"substituted")
    elif tamper=="interpreter":(env/"bin/python").write_bytes(b"substituted")
    elif tamper=="package":next(env.rglob("__init__.py")).write_bytes(b"substituted")
    elif tamper=="manifest":path.write_bytes(b'{"schema":1,"schema":2}')
    else:path.with_suffix(".sig").unlink()
    assert r.diagnostic(root,SHA,mode,expected_manifest=r._digest(r._canonical(manifest)))["state"]=="MISMATCH"


@pytest.mark.parametrize("target",["signature","public","artifact","descriptor_directory"])
def test_filesystem_replacement_after_signature_check_invalidates_the_window(signed_runtime,monkeypatch,target):
    root,env,mode,manifest,path=signed_runtime;run=p.subprocess.run
    file={"signature":path.with_suffix(".sig"),"public":root/"runtime-trust/artifacts-ed25519.pub",
        "artifact":next((root/"wheelhouse").iterdir()),"descriptor_directory":path.parent}[target]
    def replace(*a,**kw):
        result=run(*a,**kw)
        old=file.with_name(file.name+"-retained");file.rename(old)
        if old.is_dir():shutil.copytree(old,file)
        else:file.write_bytes(old.read_bytes())
        return result
    import shutil
    monkeypatch.setattr(p.subprocess,"run",replace)
    assert r.diagnostic(root,SHA,mode,expected_manifest=r._digest(r._canonical(manifest)))["state"]=="MISMATCH"
