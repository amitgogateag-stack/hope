from unittest.mock import Mock

import pytest

from hope.api import release_attestation as module


def documents():
    sha="a"*40
    return {"/branches/p0-pit-context":{"commit":{"sha":sha}},
        "/branches/day29-green-baseline":{"commit":{"sha":"7e9d341742818a97accce3e2301b1c7f96596795"}},
        "/actions/runs/123":{"id":123,"run_attempt":1,"head_sha":sha,"head_branch":"p0-pit-context",
            "event":"push","name":"HOPE CI","status":"completed","conclusion":"success",
            "repository":{"full_name":"amitgogateag-stack/hope"}},
        "/issues/5/comments?per_page=100":[{"user":{"type":"Bot"},"body":
            f"HOPE_CI_STATUS_V1\nsha={sha}\nrun_id=123\nrun_attempt=1\ntest_result=success\nevent=push"}]}


def test_exact_attestation_requires_all_three_authorities(monkeypatch):
    values=documents(); calls=[]
    def fetch(path):
        calls.append(path);return values[path]
    monkeypatch.setattr(module,"_fetch",fetch)
    result=module.verify_exact_ci("a"*40,123,1)
    assert len(calls)==4 and result["run_id"]==123


@pytest.mark.parametrize("field,value",[("head_sha","b"*40),("head_branch","other"),
    ("event","pull_request"),("name","other"),("status","in_progress"),
    ("conclusion","failure"),("run_attempt",2),("repository",{"full_name":"other/repo"})])
def test_mismatched_ci_fails_closed(monkeypatch,field,value):
    values=documents();values["/actions/runs/123"][field]=value
    monkeypatch.setattr(module,"_fetch",lambda path:values[path])
    with pytest.raises(ValueError,match="EXACT_HEAD_CI_REQUIRED"):
        module.verify_exact_ci("a"*40,123,1)


@pytest.mark.parametrize("kind",["human","old","failed","absent"])
def test_marker_must_match_bot_and_exact_attempt(monkeypatch,kind):
    values=documents();comments=values["/issues/5/comments?per_page=100"]
    if kind=="human":comments[0]["user"]["type"]="User"
    elif kind=="old":comments[0]["body"]=comments[0]["body"].replace("run_attempt=1","run_attempt=2")
    elif kind=="failed":comments[0]["body"]=comments[0]["body"].replace("test_result=success","test_result=failure")
    else:comments.clear()
    monkeypatch.setattr(module,"_fetch",lambda path:values[path])
    with pytest.raises(ValueError,match="MARKER_REQUIRED"):
        module.verify_exact_ci("a"*40,123,1)


@pytest.mark.parametrize("sha,run,attempt",[("invalid",1,1),("a"*40,True,1),
    ("a"*40,1,0),("7e9d341742818a97accce3e2301b1c7f96596795",1,1)])
def test_invalid_and_frozen_identity_never_uses_network(monkeypatch,sha,run,attempt):
    fetch=Mock();monkeypatch.setattr(module,"_fetch",fetch)
    with pytest.raises(ValueError):module.verify_exact_ci(sha,run,attempt)
    fetch.assert_not_called()


def test_branch_advance_is_not_accepted(monkeypatch):
    values=documents();values["/branches/p0-pit-context"]["commit"]["sha"]="b"*40
    monkeypatch.setattr(module,"_fetch",lambda path:values[path])
    with pytest.raises(ValueError,match="CI_REQUIRED"):module.verify_exact_ci("a"*40,123,1)
