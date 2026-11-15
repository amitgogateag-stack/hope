"""Real signatures, installed bytes, journal/fsync/locks and killed lab workers."""
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from hope.api import deployment_rehearsal as lab,dashboard_activation as activation
from hope.api import runtime_integrity as r,package_provenance as p
from test_package_provenance import signed_runtime,SHA

TARGET="3"*40


def stage(root,sha,mode,template,private):
    release=root/"releases"/sha
    if not release.exists():shutil.copytree(root/"releases"/SHA,release)
    env=r.environment_path(root,sha,mode)
    if not env.exists():shutil.copytree(template,env)
    base=r.build_runtime_manifest(root,sha,mode)
    descriptor=p.build_candidate(root,sha,mode,{item.name:item.read_bytes() for item in (root/"wheelhouse").iterdir()},runtime_manifest=base)
    path=root/"artifact-provenance"/(sha+"-"+mode+".json");path.write_bytes(r._canonical(descriptor))
    subprocess.run([str(p.OPENSSL),"pkeyutl","-sign","-rawin","-inkey",str(private),"-in",str(path),"-out",str(path.with_suffix(".sig"))],check=True,capture_output=True)
    manifest=r.build_runtime_manifest(root,sha,mode,provenance_digest=r._digest(path.read_bytes()))
    r.manifest_path(root,sha,mode).write_bytes(r._canonical(manifest))


@pytest.fixture
def rehearsal(signed_runtime,monkeypatch):
    root,env,mode,_,_=signed_runtime;monkeypatch.setattr(activation,"OWNER",os.geteuid())
    private=root.parent/"fixture-only.key"
    stage(root,SHA,"shared",env,private);stage(root,TARGET,"release",env,private)
    (root/".hope-rehearsal").write_bytes(lab.MARKER);(root/"units").mkdir()
    (root/"current").symlink_to(root/"releases"/SHA,target_is_directory=True)
    layout=activation.Layout(root=root,units=root/"units",snapshot=root/"snapshot.json",health=root/"health.json",environment=root/"reader.env")
    def host():
        return lab.RehearsalHost(layout,database_probe=lambda sha:dict(release_commit=sha,read_only=True,schema="COMPATIBLE"),
            health_probe=lambda sha:dict(release_commit=sha,worker="CURRENT",snapshot="CURRENT",reconciliation="CLEAR",live_enabled=False),
            # These are explicitly synthetic SHA fixtures, not Git releases.
            # Real Git authentication remains tested by operations deployment.
            source_verifier=lambda release,sha:None)
    return layout,host


def test_clean_first_dashboard_upgrade_rollback_and_repeated_recovery(rehearsal):
    layout,make_host=rehearsal;host=make_host()
    result=lab.rehearse_upgrade(layout,host,target=TARGET,shared=SHA,ci_run=123,ci_attempt=1)
    assert result["state"]=="UNVERIFIED" and result["scope"]=="DISPOSABLE_LAB_ONLY" and result["execution_authorized"] is False
    assert result['lab_result']=='COMPLETED' and result['independent_acceptance']=='UNVERIFIED'
    assert activation._record(layout)["schema"]=="hope.dashboard-activation.v3"
    activation.rollback(layout,host,target=TARGET,shared=SHA)
    assert activation._record(layout) is None and activation._pointer(layout,"current")==SHA
    assert activation._pointer(layout,"dashboard-current",optional=True) is None
    with pytest.raises(RuntimeError,match="RECOVERY_IDENTITY"):activation.rollback(layout,host,target=TARGET,shared=SHA)
    assert r.attest_runtime(layout.root,SHA,"shared")["provenance_state"]=="VERIFIED"
    # A fresh explicit attempt after completed recovery remains possible.
    lab.rehearse_upgrade(layout,make_host(),target=TARGET,shared=SHA,ci_run=123,ci_attempt=1)


@pytest.mark.parametrize("fault",["manifest","interpreter","package","database_missing","database_identity","database_writable","health","live","stale_snapshot"])
def test_rehearsal_faults_never_become_acceptance(rehearsal,fault):
    layout,make_host=rehearsal;host=make_host();root=layout.root
    if fault=="manifest":r.manifest_path(root,TARGET,"release").write_bytes(b"partial{")
    elif fault=="interpreter":(r.environment_path(root,TARGET,"release")/"bin/python").write_bytes(b"tampered")
    elif fault=="package":next(r.environment_path(root,TARGET,"release").rglob("__init__.py")).write_bytes(b"tampered")
    elif fault.startswith("database"):
        host.database_probe=lambda sha:None if fault=="database_missing" else dict(release_commit="4"*40 if fault=="database_identity" else sha,read_only=fault!="database_writable",schema="COMPATIBLE")
    else:
        host.health_probe=lambda sha:dict(release_commit=sha,worker="CURRENT",snapshot="STALE" if fault=="stale_snapshot" else "CURRENT",reconciliation="UNKNOWN" if fault=="health" else "CLEAR",live_enabled=fault=="live")
    with pytest.raises((OSError,ValueError,RuntimeError)):lab.rehearse_upgrade(layout,host,target=TARGET,shared=SHA,ci_run=123,ci_attempt=1)
    record=activation._record(layout)
    assert record is None or record["phase"]=="ROLLBACK_FAILED"
    assert activation._pointer(layout,"current")==SHA
    assert not (root/"rehearsal-result.json").exists()


@pytest.mark.parametrize("phase",["PREPARED","CONFIGS","POINTER","VERIFYING","STARTED","COMMITTED"])
def test_native_termination_requires_explicit_verified_rollback(rehearsal,phase):
    layout,make_host=rehearsal
    def child():
        host=make_host();save=activation._save
        def saved(path,value,*a):
            result=save(path,value,*a)
            if path==layout.journal and value.get("phase")==phase:os._exit(77)
            return result
        activation._save=saved
        if phase in {"CONFIGS","POINTER"}:
            name="_install_configs" if phase=="CONFIGS" else "_point";original=getattr(activation,name)
            def killed(*a,**kw):original(*a,**kw);os._exit(77)
            setattr(activation,name,killed)
        if phase=="STARTED":host.verify=lambda *a,**kw:os._exit(77)
        lab.rehearse_upgrade(layout,host,target=TARGET,shared=SHA,ci_run=123,ci_attempt=1)
    process=multiprocessing.get_context("fork").Process(target=child);process.start();process.join(15)
    try:
        assert process.exitcode==77
        record=activation._record(layout);assert not activation._owner_alive(record)
        if phase!="COMMITTED":
            with pytest.raises(RuntimeError,match="RECOVERY_REQUIRED"):
                lab.rehearse_upgrade(layout,make_host(),target=TARGET,shared=SHA,ci_run=123,ci_attempt=1)
        activation.rollback(layout,make_host(),target=TARGET,shared=SHA)
        assert activation._record(layout) is None and activation._pointer(layout,"current")==SHA
        assert r.attest_runtime(layout.root,SHA,"shared")["provenance_state"]=="VERIFIED"
    finally:
        if process.is_alive():process.kill();process.join(5)


def test_conflicting_concurrent_activation_cannot_reach_host(rehearsal):
    layout,make_host=rehearsal
    from hope.api.operations_deployment import _activation_guard,DeploymentError
    with _activation_guard(layout.root):
        host=make_host()
        with pytest.raises(DeploymentError,match="LOCKED"):
            lab.rehearse_upgrade(layout,host,target=TARGET,shared=SHA,ci_run=123,ci_attempt=1)
        assert host.calls==[] and activation._record(layout) is None
