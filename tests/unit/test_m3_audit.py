from unittest.mock import Mock
from urllib.error import HTTPError

from hope.api import m3_audit as module


def test_systemd_output_is_whitelisted_and_never_runs_mutations(monkeypatch):
    runner=Mock(return_value=Mock(stdout="ActiveState=inactive\nSubState=dead\nUnitFileState=disabled\nEnvironment=secret"))
    monkeypatch.setattr(module.subprocess,"run",runner)
    result=module._unit("hope-paper.service")
    assert result=={"ActiveState":"inactive","SubState":"dead","UnitFileState":"disabled"}
    assert runner.call_args.args[0][:2]==["systemctl","show"]
    assert "secret" not in str(result)


def test_systemd_permission_failure_is_unavailable_not_raw_error(monkeypatch):
    monkeypatch.setattr(module.subprocess,"run",Mock(side_effect=PermissionError("secret")))
    assert module._unit("hope-paper.service")=={"status":"UNAVAILABLE"}


def test_http_503_remains_explicit_without_echoing_body(monkeypatch):
    opener=Mock();opener.open.side_effect=HTTPError("private",503,"secret",{},None)
    monkeypatch.setattr(module,"build_opener",lambda *a:opener)
    assert module._endpoint("/operations.json")=={"http_status":503,"status":"UNAVAILABLE"}


def test_http_success_filters_raw_metadata(monkeypatch):
    response=Mock();response.status=200
    response.read.return_value=b'{"service":"RUNNING","data_feed":"UNAVAILABLE","password":"secret","state":"secret"}'
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    opener=Mock();opener.open.return_value=response
    monkeypatch.setattr(module,"build_opener",lambda *a:opener)
    assert module._endpoint("/status.json")=={"http_status":200,"service":"RUNNING","data_feed":"UNAVAILABLE"}
    assert opener.open.call_args.args[0]=="http://127.0.0.1:8788/status.json"
