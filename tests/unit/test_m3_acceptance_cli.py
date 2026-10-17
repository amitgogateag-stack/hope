from unittest.mock import Mock
from uuid import uuid4

import pytest

from hope.api import paper_acceptance as module


def arguments():
    return ["--approve-one-paper-cycle","--expected-database","hope_m3_restore_test","--release","unused",
        "--expected-sha","a"*40,"--ci-run-id","123","--ci-run-attempt","1","--factory","reviewed_test:factory",
        "--run-id",str(uuid4()),"--evidence-dir",str(__import__('pathlib').Path.cwd().parent/"unused-evidence")]


@pytest.mark.parametrize("bad",["approval","database","live","mode","commit","ci","release"])
def test_cli_rejects_before_factory_or_database(monkeypatch,bad,capsys):
    args=arguments()
    for key,value in {"HOPE_EXECUTION_MODE":"PAPER","HOPE_LIVE_ENABLED":"false",
        "HOPE_DEPLOYED_COMMIT":"a"*40,"HOPE_DATABASE_URL":"never-print-test-dsn"}.items():monkeypatch.setenv(key,value)
    inspect=Mock();attest=Mock()
    monkeypatch.setattr(module,"inspect_release",inspect);monkeypatch.setattr(module,"verify_exact_ci",attest)
    load=Mock();engine=Mock();monkeypatch.setattr(module,"load_plan_factory",load);monkeypatch.setattr(module,"create_engine",engine)
    if bad=="approval":args.remove("--approve-one-paper-cycle")
    elif bad=="database":args[args.index("hope_m3_restore_test")]="production"
    elif bad=="live":monkeypatch.setenv("HOPE_LIVE_ENABLED","true")
    elif bad=="mode":monkeypatch.setenv("HOPE_EXECUTION_MODE","LIVE")
    elif bad=="commit":monkeypatch.setenv("HOPE_DEPLOYED_COMMIT","b"*40)
    elif bad=="ci":attest.side_effect=ValueError("secret failure")
    elif bad=="release":inspect.side_effect=ValueError("secret failure")
    with pytest.raises(SystemExit) as exc:module.main(args)
    assert exc.value.code==2;load.assert_not_called();engine.assert_not_called()
    captured=capsys.readouterr()
    assert "never-print-test-dsn" not in captured.out+captured.err
    assert "secret failure" not in captured.out+captured.err
