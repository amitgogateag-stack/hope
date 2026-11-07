"""Real CPython venvs, offline wheels, Git source and external ephemeral keys."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import time

import pytest
from hope.api import release_builder as b,release_acceptance as a,runtime_integrity as r,package_provenance as p
from test_package_provenance import wheel
from build_approval_fixture import sign


@pytest.fixture(scope="module")
def trusted_python(tmp_path_factory):
    if os.name!="posix":pytest.skip("Native isolated CPython staging is exercised by Linux CI")
    # Runner tool caches can have shared writable/hardlinked executables. Do
    # not relax the production trust policy: prepare an isolated, byte-matched
    # fixture distribution instead. No system installation is changed.
    root=tmp_path_factory.mktemp("m4-python");(root/"bin").mkdir();(root/"lib").mkdir()
    executable=Path(sys.executable).resolve(strict=True);target=root/"bin/python3.13"
    shutil.copyfile(executable,target);target.chmod(0o755)
    assert r._digest(target.read_bytes())==r._digest(executable.read_bytes())
    standard=Path(sysconfig.get_path("stdlib"))
    shutil.copytree(standard,root/"lib/python3.13",ignore=shutil.ignore_patterns("site-packages","__pycache__","*.pyc","test","tests"))
    for library in Path(sysconfig.get_config_var("LIBDIR")).glob("libpython3.13*.so*"):
        if library.is_file():shutil.copyfile(library,root/"lib"/library.name)
    for path in root.rglob("*"):
        if not path.is_symlink():path.chmod(0o755 if path.is_dir() or path==target else 0o644)
    return target


@pytest.fixture
def build_case(tmp_path,monkeypatch,trusted_python):
    if os.name!="posix":pytest.skip("Actual isolated POSIX builder/Ed25519/Git and Python 3.13 are exercised by Linux CI")
    monkeypatch.setattr(r,"OWNER",os.geteuid())
    source=tmp_path/"source";(source/"src").mkdir(parents=True);(source/"constraints").mkdir()
    (source/"src/fixture.py").write_text("# Synthetic acceptance source, never a trading factory\n")
    for name in ("src/hope/api/paper_service.py","src/hope/api/dashboard_server.py","src/hope/api/dashboard_snapshot.py",
            "docs/dashboard/preview.html","deploy/operations/hope-paper.service","migrations/001_fixture.sql"):
        target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text("# Synthetic source fixture; never executed as a service\n")
    (source/"constraints/ci-test.txt").write_text("\n".join(name+"==1.0" for name in sorted(r.REQUIRED)))
    env=dict(b.operations.GIT_ENVIRONMENT,GIT_AUTHOR_DATE="2025-01-01T00:00:00Z",GIT_COMMITTER_DATE="2025-01-01T00:00:00Z")
    for args in (["init"],["remote","add","origin","https://github.com/amitgogateag-stack/hope.git"],["add","."],["-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","-m","synthetic source"]):
        subprocess.run([b.operations.GIT_BINARY,"-C",str(source),*args],env=env,check=True,capture_output=True)
    sha=b.operations._git(source,"rev-parse","HEAD")
    authority=tmp_path/"authority";authority.mkdir();(authority/"wheelhouse").mkdir()
    private=tmp_path/"fixture-builder.key";public=authority/"builder.pub"
    subprocess.run([str(p.OPENSSL),"genpkey","-algorithm","ED25519","-out",str(private)],check=True,capture_output=True)
    subprocess.run([str(p.OPENSSL),"pkey","-in",str(private),"-pubout","-out",str(public)],check=True,capture_output=True)
    artifacts=[]
    for name in sorted(r.REQUIRED):
        payload,_=wheel(name);filename=name+"-1.0-py3-none-any.whl";(authority/"wheelhouse"/filename).write_bytes(payload)
        artifacts.append(dict(filename=filename,sha256=r._digest(payload),name=name,version="1.0"))
    deployment=tmp_path/"deployment";deployment.mkdir();(deployment/".hope-acceptance").write_bytes(a.MARKER)
    plan=dict(schema="hope.build-input.v1",release_commit=sha,builder_id="b"*64,builder=b.builder_identity(trusted_python),
        epoch=1735689600,deployment_root=str(deployment),artifacts=artifacts,requested_extras={})
    path=authority/"inputs.json";path.write_bytes(r._canonical(plan));sign(path,private)
    work=tmp_path/"work";work.mkdir();(work/".hope-builder").write_bytes(b.MARKER)
    return dict(source=source,work=work,inputs=path,signature=path.with_suffix(".sig"),public=public,
        expected_inputs_digest=r._digest(path.read_bytes()),python=trusted_python),plan,private,deployment


def test_two_real_offline_builds_are_byte_identical_candidates_and_stage_safely(build_case):
    arguments,plan,private,deployment=build_case
    result=b.build_release(**arguments)
    assert result["state"]=="REPRODUCIBLE" and result["approved"] is False and result["execution_authorized"] is False
    work=arguments["work"];sha=plan["release_commit"]
    assert (work/"a/release.tar").read_bytes()==(work/"b/release.tar").read_bytes()
    release,environment=a.stage_lab_release(work/"a/release.tar",deployment,arguments["source"],sha,expected_digest=result["hashes"]["release.tar"])
    b.operations.inspect_release(release,sha)
    r._require((environment/"lib/python3.13/site-packages/sqlalchemy/__init__.py").read_bytes()==b"approved = True\n","TEST_CONTENT_MISMATCH")
    code="import sys;assert sys.version_info[:2]==(3,13);import sqlalchemy;assert sqlalchemy.approved is True"
    subprocess.run([str(environment/"bin/python"),"-I","-B","-c",code],check=True,capture_output=True,timeout=10)
    # Merely building/staging never creates runtime or release approval.
    assert not (deployment/"runtime-manifests").exists()
    with pytest.raises(RuntimeError,match="STAGE_EXISTS"):
        a.stage_lab_release(work/"a/release.tar",deployment,arguments["source"],sha,expected_digest=result["hashes"]["release.tar"])


@pytest.mark.parametrize("fault",["signature","artifact","source","builder","authority_overlap","prefix"])
def test_unapproved_or_changed_inputs_fail_before_emitting_candidates(build_case,fault):
    args,plan,private,deployment=build_case
    if fault=="signature":args["signature"].write_bytes(b"x"*64)
    elif fault=="artifact":next((args["inputs"].parent/"wheelhouse").iterdir()).write_bytes(b"corrupt")
    elif fault=="source":(args["source"]/"src/fixture.py").write_text("changed")
    elif fault=="authority_overlap":args["source"]=args["work"]
    else:
        if fault=="builder":plan["builder"]["python_sha256"]="f"*64
        else:plan["deployment_root"]="/tmp/../unsafe"
        args["inputs"].write_bytes(r._canonical(plan));sign(args["inputs"],private);args["expected_inputs_digest"]=r._digest(args["inputs"].read_bytes())
    with pytest.raises((RuntimeError,OSError,ValueError)):b.build_release(**args)
    assert not (args["work"]/"candidate.json").exists()


def test_second_build_nondeterminism_is_not_approved(build_case,monkeypatch):
    args,_,_,_=build_case;install=b._install;count=0
    def unstable(*a,**kw):
        nonlocal count
        install(*a,**kw);count+=1
        if count==2:(a[0]/"lib/python3.13/site-packages/sqlalchemy/__init__.py").write_bytes(b"tampered")
    monkeypatch.setattr(b,"_install",unstable)
    with pytest.raises(RuntimeError):b.build_release(**args)
    assert not (args["work"]/"candidate.json").exists()


def test_real_worker_process_identity_changes_on_restart(build_case):
    args,plan,_,_=build_case;b.build_release(**args)
    python=args["work"]/"a/release-venvs"/plan["release_commit"]/"bin/python"
    arguments=["-I","-B","-c","import time;time.sleep(60)"]
    old=None
    for _ in range(2):
        process=subprocess.Popen([str(python),*arguments])
        try:
            proof=a.process_observation(process.pid,python,arguments)
            assert len(proof)==64 and proof!=old;old=proof
        finally:process.kill();process.wait(5)
        with pytest.raises((FileNotFoundError,RuntimeError)):a.process_observation(process.pid,python,arguments)


def test_archive_tampering_cannot_stage_a_release(build_case):
    args,plan,_,deployment=build_case;report=b.build_release(**args)
    archive=args["work"]/"a/release.tar";archive.write_bytes(b"not approved")
    with pytest.raises(RuntimeError,match="ARCHIVE_MISMATCH"):
        a.stage_lab_release(archive,deployment,args["source"],plan["release_commit"],expected_digest=report["hashes"]["release.tar"])
    assert not (deployment/"releases").exists()
