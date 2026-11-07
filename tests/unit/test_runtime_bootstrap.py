"""Control runtime authentication precedes all inspected environment execution."""
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hope.api import runtime_bootstrap as m

SHA="2"*40


@pytest.fixture
def control(tmp_path,monkeypatch):
    release=tmp_path/"releases"/SHA;release.mkdir(parents=True)
    monkeypatch.setattr(m.operations,"_activation_guard",lambda *a,**k:nullcontext())
    inspector=Mock();monkeypatch.setattr(m.operations,"inspect_release",inspector)
    snapshot=Mock(return_value={"source":"stable"});monkeypatch.setattr(m,"source_snapshot",snapshot)
    attestor=Mock(return_value={"manifest_sha256":"a"*64,"provenance_state":"VERIFIED"});monkeypatch.setattr(m,"attest_runtime",attestor)
    return release,inspector,snapshot,attestor


@pytest.mark.parametrize("mode",["shared","release"])
def test_control_runtime_has_independent_static_checks_and_minimal_exec_environment(control,monkeypatch,mode):
    release,inspector,snapshot,attestor=control
    monkeypatch.setenv("GH_TOKEN","test-token")
    monkeypatch.setenv("PYTHONPATH","/untrusted");monkeypatch.setenv("LD_PRELOAD","/untrusted")
    execute=Mock(return_value=17)
    assert m.handoff(release,["preflight","--target",SHA],mode,execute=execute)==17
    python=m.environment_path(release.parent.parent,SHA,mode)/"bin/python"
    assert inspector.call_count==2 and snapshot.call_count==2
    attestor.assert_called_once_with(release.parent.parent,SHA,mode,expected_manifest=None,release=release)
    executable,argv,environment=execute.call_args.args
    assert executable==str(python)
    assert argv==[str(python),"-I","-B","-X",m.CACHE_PREFIX,str(release/"deploy/operations/dashboard_release.py"),
        "--control-runtime",mode,"--control-manifest","a"*64,"preflight","--target",SHA]
    assert environment=={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LANG":"C","LC_ALL":"C","GH_TOKEN":"test-token"}


@pytest.mark.parametrize("failure",["git_initial","git_closing","manifest","source_changed"])
def test_no_venv_execution_after_attestation_or_concurrent_release_failure(control,failure):
    release,inspector,snapshot,attestor=control
    if failure=="git_initial":inspector.side_effect=RuntimeError("private path")
    elif failure=="git_closing":inspector.side_effect=[None,RuntimeError("private path")]
    elif failure=="manifest":attestor.side_effect=RuntimeError("private path")
    else:snapshot.side_effect=[{"a":1},{"a":2}]
    execute=Mock()
    with pytest.raises(RuntimeError):m.handoff(release,["preflight"],"shared",execute=execute)
    execute.assert_not_called()


@pytest.mark.parametrize("failure",["isolation","site","attestation","prefix","version"])
def test_bootstrap_failures_are_sanitized_before_activation_import(control,monkeypatch,capsys,failure):
    release,_,_,attestor=control
    monkeypatch.setattr(m,"sys",SimpleNamespace(flags=SimpleNamespace(isolated=failure!="isolation",
        no_site=failure!="site"),version_info=(3,12) if failure=="version" else (3,13)))
    verifier=Mock(side_effect=RuntimeError("private secret") if failure=="prefix" else None)
    monkeypatch.setattr(m,"verify_interpreter",verifier)
    if failure=="attestation":attestor.side_effect=RuntimeError("private secret")
    args=["preflight"] if failure in {"isolation","site"} else ["--control-manifest","a"*64,"status"]
    with pytest.raises(SystemExit) as error:m.main(args,release=release)
    assert error.value.code==2
    output=capsys.readouterr().err
    assert output=="DASHBOARD_BOOTSTRAP_BLOCKED REVIEW_SOURCE_AND_RUNTIME_MANIFEST\n"


def test_second_stage_reauthenticates_pinned_manifest_before_delegating(control,monkeypatch):
    from hope.api import dashboard_activation
    release,_,_,attestor=control
    monkeypatch.setattr(m,"sys",SimpleNamespace(flags=SimpleNamespace(isolated=True,no_site=False),version_info=(3,13)))
    monkeypatch.setattr(m,"verify_interpreter",Mock())
    activate=Mock(return_value=9);monkeypatch.setattr(dashboard_activation,"main",activate)
    assert m.main(["--control-runtime","release","--control-manifest","a"*64,"status"],release=release)==9
    assert attestor.call_args.kwargs["expected_manifest"]=="a"*64
    activate.assert_called_once_with(["status"])


def test_os_bootstrap_imports_without_site_packages_or_sqlalchemy():
    source=Path(__file__).parents[2]/"src"
    program="import sys,json;sys.path.insert(0,"+repr(str(source))+");import hope.api.runtime_bootstrap;"+\
        "print(json.dumps({'site':sys.flags.no_site,'sqlalchemy':'sqlalchemy' in sys.modules,'activation':'hope.api.dashboard_activation' in sys.modules}))"
    result=subprocess.run([sys.executable,"-I","-B","-S","-X",m.CACHE_PREFIX,"-c",program],
        check=True,capture_output=True,text=True,timeout=10)
    assert json.loads(result.stdout)=={"site":1,"sqlalchemy":False,"activation":False}
