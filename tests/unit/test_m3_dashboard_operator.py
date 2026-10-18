"""Operator dispatcher contract; native execution uses a copied sudo stub only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import re

import pytest

SCRIPT=Path(__file__).parents[2]/"deploy/operations/m3_dashboard_operator.sh"


def test_dispatcher_contains_only_dashboard_bootstrap_and_isolated_python():
    content=SCRIPT.read_text()
    assert '"$ROOT/venv/bin/python" -I -B' in content
    assert "--approve-dashboard-change" in content
    assert "hope-paper.service" not in content
    commands="\n".join(line for line in content.splitlines() if not line.lstrip().startswith("#"))
    assert "systemctl" not in commands and "source " not in commands


@pytest.mark.skipif(shutil.which("bash") is None,reason="Bash syntax verified in Linux CI")
def test_consolidated_paste_blocks_parse_without_executing_host_commands():
    document=(SCRIPT.parents[2]/"docs/m3-final-operator-package.md").read_text()
    blocks=re.findall(r"```bash\n(.*?)```",document,re.S)
    assert len(blocks)==3
    for block in blocks:
        subprocess.run(["bash","-n"],input=block,text=True,check=True,capture_output=True,timeout=5)


@pytest.mark.skipif(os.name!="posix" or shutil.which("bash") is None,reason="Linux dispatcher; exercised on CI")
@pytest.mark.parametrize("action,approved,allowed", [
    ("preflight",False,True),("status",False,True),("paper-audit",False,True),("activate",False,False),
    ("activate",True,True),("rollback",False,False),("rollback",True,True),
    ("migrate",False,False),
])
def test_dispatcher_requires_approval_and_never_runs_a_host_command_in_tests(tmp_path,action,approved,allowed):
    root=tmp_path/"root";target="a"*40;shared="b"*40
    release=root/"releases"/target;bootstrap=release/"deploy/operations/dashboard_release.py"
    bootstrap.parent.mkdir(parents=True);bootstrap.write_text("reviewed")
    record=tmp_path/"record"
    sudo=tmp_path/"sudo-stub"
    sudo.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > '"+str(record)+"'\n")
    sudo.chmod(0o700)
    script=tmp_path/"operator.sh"
    script.write_text(SCRIPT.read_text().replace("ROOT=/opt/hope-operations","ROOT="+str(root))
        .replace("/usr/bin/sudo",str(sudo)))
    env={"PATH":"/usr/bin:/bin","HOPE_DASHBOARD_TARGET":target,"HOPE_DASHBOARD_SHARED":shared,
        "HOPE_DASHBOARD_CI_RUN_ID":"123","HOPE_DASHBOARD_CI_ATTEMPT":"1"}
    arguments=["bash",str(script),action]+(["--approve-dashboard-change"] if approved else [])
    result=subprocess.run(arguments,env=env,capture_output=True,timeout=5)
    assert (result.returncode==0)==allowed
    assert record.exists()==allowed
    if allowed:
        values=record.read_text().splitlines()
        assert values[:5]==["--preserve-env=GH_TOKEN",str(root/"venv/bin/python"),"-I","-B",str(bootstrap)]
        assert values[5]==action and "hope-paper" not in " ".join(values)
        assert ("--ci-run-id" in values)==(action in {"preflight","activate"})
        assert ("--approve-dashboard-change" in values)==approved
