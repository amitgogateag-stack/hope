"""Profile inputs and denied-session ordering; real SQL is tested separately."""
from dataclasses import FrozenInstanceError
from unittest.mock import Mock
import pytest
from hope.security import dashboard_observer as guard
from hope.api import dashboard_snapshot_refresh as refresh

@pytest.mark.parametrize("principal,digest", [("", "a"*64),("Owner", "a"*64),("a b", "a"*64),
    ("a"*64,"a"*64),(True,"a"*64),("reader", "A"*64),("reader", "x"*64),
    ("reader", "a"*63),("reader",None),("reader\ud800","a"*64)])
def test_untrusted_profile_is_refused(principal,digest):
    with pytest.raises(ValueError,match="DASHBOARD_OBSERVER_PROFILE_INVALID"):
        guard.ObserverProfile(principal,digest)

def test_profile_is_immutable_and_not_authority():
    p=guard.ObserverProfile("reader","a"*64)
    with pytest.raises(FrozenInstanceError):p.principal="owner"
    assert not hasattr(p,"execution_authorized")

@pytest.mark.parametrize("role", [(False,True,True),(True,False,True),(True,True,False)])
def test_actual_role_refusal_precedes_capability_and_identity_checks(monkeypatch,role):
    connection=Mock();connection.execute.return_value.one.return_value=role
    closure=Mock();monkeypatch.setattr(guard,"_require_public_capability_closure",closure)
    with pytest.raises(ValueError,match="DASHBOARD_OBSERVER_ROLE_UNSAFE"):
        guard.verify_observer(connection,guard.ObserverProfile("reader","a"*64))
    closure.assert_not_called()
    assert connection.execute.call_count==1

def test_refresh_without_profile_refuses_before_files_or_export(tmp_path,monkeypatch):
    export=Mock();monkeypatch.setattr(refresh,"export_dashboard_snapshot",export)
    with pytest.raises(TypeError):
        refresh.refresh_once(tmp_path/'data',tmp_path/'status','not-accessed',expected_database='lab',deployed_commit='a'*40)
    export.assert_not_called();assert list(tmp_path.iterdir())==[]

@pytest.mark.parametrize("value", [None,{},True])
def test_supplied_boolean_or_worker_dict_is_not_a_profile(tmp_path,monkeypatch,value):
    export=Mock();monkeypatch.setattr(refresh,"export_dashboard_snapshot",export)
    with pytest.raises(ValueError,match="DASHBOARD_OBSERVER_PROFILE_INVALID"):
        refresh.refresh_once(tmp_path/'data',tmp_path/'status','not-accessed',expected_database='lab',deployed_commit='a'*40,observer=value)
    export.assert_not_called();assert list(tmp_path.iterdir())==[]
