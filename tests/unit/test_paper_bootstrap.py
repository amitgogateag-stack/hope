from contextlib import nullcontext
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from hope.api import paper_bootstrap as m

SHA="2"*40


@pytest.fixture
def launch(tmp_path,monkeypatch):
    release=tmp_path/"releases"/SHA
    monkeypatch.setenv("HOPE_DEPLOYED_COMMIT",SHA);monkeypatch.setenv("HOPE_LIVE_ENABLED","false")
    monkeypatch.setenv("PYTHONPATH","untrusted");monkeypatch.setenv("LD_PRELOAD","untrusted")
    monkeypatch.setattr(m,"sys",SimpleNamespace(flags=SimpleNamespace(isolated=True,no_site=True),version_info=(3,13)))
    authenticate=Mock(return_value=(tmp_path/"venv/bin/python",{"manifest_sha256":"a"*64}))
    monkeypatch.setattr(m,"authenticate_control",authenticate)
    return release,authenticate


@pytest.mark.parametrize("preflight",[False,True])
def test_os_handoff_is_pinned_and_does_not_forward_import_or_loader_input(launch,preflight):
    release,authenticate=launch;execute=Mock(return_value=7)
    assert m.main(["--preflight"] if preflight else ["--factory","reviewed:factory"],release=release,execute=execute)==7
    binary,args,env=execute.call_args.args
    assert args[:5]==[binary,"-I","-B","-X",m.CACHE_PREFIX]
    assert args[6:8]==["--approved-runtime-manifest","a"*64]
    assert ("--preflight" in args)==preflight and "PYTHONPATH" not in env and "LD_PRELOAD" not in env
    assert env["HOPE_LIVE_ENABLED"]=="false"


@pytest.mark.parametrize("fault",["signature","isolation","site","live","commit"])
def test_no_unapproved_runtime_or_live_path_reaches_exec(launch,monkeypatch,capsys,fault):
    release,authenticate=launch;execute=Mock()
    if fault=="signature":authenticate.side_effect=RuntimeError("private path secret")
    elif fault in {"isolation","site"}:monkeypatch.setattr(m,"sys",SimpleNamespace(flags=SimpleNamespace(isolated=fault!="isolation",no_site=fault!="site")))
    elif fault=="live":monkeypatch.setenv("HOPE_LIVE_ENABLED","true")
    else:monkeypatch.setenv("HOPE_DEPLOYED_COMMIT","3"*40)
    with pytest.raises(SystemExit) as error:m.main([],release=release,execute=execute)
    assert error.value.code==2 and "secret" not in capsys.readouterr().err;execute.assert_not_called()


@pytest.mark.parametrize("preflight",[False,True])
def test_reauthenticated_second_stage_keeps_scoped_database_preflight_and_worker_gate(launch,monkeypatch,preflight):
    from hope.api import operations_deployment,paper_service
    release,authenticate=launch
    monkeypatch.setattr(m,"verify_interpreter",Mock())
    run=Mock(return_value=0);monkeypatch.setattr(operations_deployment if preflight else paper_service,"main",run)
    args=["--approved-runtime-manifest","a"*64]+(["--preflight"] if preflight else ["--once"])
    assert m.main(args,release=release)==0
    assert authenticate.call_args.kwargs["expected_manifest"]=="a"*64
    if preflight:assert run.call_args.args[0][-2:]==["--database-component","paper"]
    else:run.assert_called_once_with(["--once"])
