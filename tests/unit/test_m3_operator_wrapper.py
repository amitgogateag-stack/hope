import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("bash") is None,reason="Linux operator wrapper; exercised in Linux CI")
@pytest.mark.parametrize("approved",[False,True])
def test_wrapper_cannot_execute_without_approval_and_real_factory(tmp_path,approved):
    script=Path(__file__).parents[2]/"deploy/operations/m3_accept_once.sh"
    environment={**os.environ,"HOPE_M3_RELEASE":str(tmp_path),"HOPE_M3_PYTHON":"/unused/python",
        "HOPE_DEPLOYED_COMMIT":"a"*40,"HOPE_M3_CI_RUN_ID":"1","HOPE_M3_CI_ATTEMPT":"1"}
    environment.pop("HOPE_PAPER_FACTORY",None)
    result=subprocess.run(["bash",str(script)]+(["--approve-one-paper-cycle"] if approved else []),
        env=environment,capture_output=True,text=True,timeout=5)
    assert result.returncode!=0
    assert list(tmp_path.iterdir())==[]


def test_wrapper_never_adds_external_factory_path(tmp_path):
    script=Path(__file__).parents[2]/"deploy/operations/m3_accept_once.sh"
    content=script.read_text()
    assert "HOPE_M3_FACTORY_PATH" not in content
    assert '"PYTHONPATH=$HOPE_M3_RELEASE/src"' in content
    assert 'exec /usr/bin/env -i "${safe_environment[@]}" /usr/bin/timeout' in content


def test_wrapper_gives_interpreter_only_reviewed_environment(tmp_path):
    script=Path(__file__).parents[2]/"deploy/operations/m3_accept_once.sh"
    release=tmp_path/"release"
    release.mkdir()
    interpreter=tmp_path/"inspect-environment"
    interpreter.write_text("#!/bin/sh\n/usr/bin/env -0\n")
    interpreter.chmod(0o700)
    environment={**os.environ,
        "HOPE_M3_RELEASE":str(release),"HOPE_M3_PYTHON":str(interpreter),
        "HOPE_DEPLOYED_COMMIT":"a"*40,"HOPE_M3_CI_RUN_ID":"123",
        "HOPE_M3_CI_ATTEMPT":"1","HOPE_PAPER_FACTORY":"reviewed.factory:build",
        "HOPE_M3_RUN_ID":"11111111-1111-1111-1111-111111111111",
        "HOPE_M3_EVIDENCE_DIR":str(tmp_path/"evidence"),
        "HOPE_EXECUTION_MODE":"PAPER","HOPE_LIVE_ENABLED":"false",
        "HOPE_DATABASE_URL":"postgresql://scoped-secret",
        "UNRELATED_SECRET":"must-not-cross","PYTHONHOME":"/hostile/python",
        "PYTHONWARNINGS":"error::UserWarning","LD_LIBRARY_PATH":"/hostile/lib"}
    result=subprocess.run(["bash",str(script),"--approve-one-paper-cycle"],
        env=environment,capture_output=True,timeout=5)
    assert result.returncode==0,result.stderr.decode(errors="replace")
    child=dict(item.split(b"=",1) for item in result.stdout.split(b"\0") if item)
    assert child=={
        b"HOME":b"/nonexistent",b"HOPE_DATABASE_URL":b"postgresql://scoped-secret",
        b"HOPE_DEPLOYED_COMMIT":b"a"*40,b"HOPE_EXECUTION_MODE":b"PAPER",
        b"HOPE_LIVE_ENABLED":b"false",b"LANG":b"C",b"LC_ALL":b"C",
        b"PATH":b"/usr/sbin:/usr/bin:/sbin:/bin",
        b"PWD":str(release).encode(),
        b"PYTHONDONTWRITEBYTECODE":b"1",b"PYTHONNOUSERSITE":b"1",
        b"PYTHONPATH":str(release/"src").encode(),
    }
