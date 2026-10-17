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
