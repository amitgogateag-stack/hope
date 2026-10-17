"""Verify fixed-repository, exact-head CI over authenticated HTTPS when configured."""
import json
import os
import re
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "amitgogateag-stack/hope"
API = f"https://api.github.com/repos/{REPOSITORY}"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _fetch(path):
    headers = {"Accept":"application/vnd.github+json", "User-Agent":"HOPE-M3-preflight"}
    token = os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with build_opener(_NoRedirect()).open(Request(API+path,headers=headers),timeout=10) as response:
        body = response.read(131073)
        if len(body)>131072:
            raise ValueError("M3_CI_RESPONSE_TOO_LARGE")
        return json.loads(body)


def verify_exact_ci(sha: str, run_id: int, attempt: int) -> dict:
    if (not isinstance(sha,str) or re.fullmatch(r"[0-9a-f]{40}",sha) is None
            or type(run_id) is not int or run_id<1 or type(attempt) is not int or attempt<1
            or sha=="7e9d341742818a97accce3e2301b1c7f96596795"):
        raise ValueError("M3_RELEASE_IDENTITY_INVALID")
    branch = _fetch("/branches/p0-pit-context")
    baseline = _fetch("/branches/day29-green-baseline")
    run = _fetch(f"/actions/runs/{run_id}")
    if (branch.get("commit",{}).get("sha")!=sha
            or baseline.get("commit",{}).get("sha")!="7e9d341742818a97accce3e2301b1c7f96596795" or run.get("head_sha")!=sha
            or run.get("head_branch")!="p0-pit-context" or run.get("event")!="push"
            or run.get("name")!="HOPE CI" or run.get("id")!=run_id
            or run.get("run_attempt")!=attempt or run.get("status")!="completed"
            or run.get("conclusion")!="success" or run.get("repository",{}).get("full_name")!=REPOSITORY):
        raise ValueError("M3_EXACT_HEAD_CI_REQUIRED")
    comments = _fetch("/issues/5/comments?per_page=100")
    required = {f"sha={sha}",f"run_id={run_id}",f"run_attempt={attempt}","test_result=success","event=push","HOPE_CI_STATUS_V1"}
    if not isinstance(comments,list) or not any(
        isinstance(c,dict) and c.get("user",{}).get("type")=="Bot"
        and required.issubset(set(c.get("body","").splitlines())) for c in comments):
        raise ValueError("M3_EXACT_HEAD_MARKER_REQUIRED")
    return dict(sha=sha,run_id=run_id,run_attempt=attempt,
                run_url=f"https://github.com/{REPOSITORY}/actions/runs/{run_id}")
