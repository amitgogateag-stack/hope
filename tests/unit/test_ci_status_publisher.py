"""Execute the actual github-script publisher against adversarial API doubles."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

WORKFLOW = Path(__file__).parents[2] / ".github/workflows/ci.yml"


def script(workflow_text=None):
    # This is the known literal github-script block, not all YAML after it.
    # Adjacent jobs/steps may contain scripts of their own and must never run.
    lines = (WORKFLOW.read_text(encoding="utf-8")
             if workflow_text is None else workflow_text).splitlines()
    start = lines.index("  publish-ci-status:") + 1
    end = next((i for i in range(start, len(lines))
                if lines[i].strip() and not lines[i].startswith("    ")), len(lines))
    job = lines[start:end]
    markers = [i for i, line in enumerate(job) if line == "          script: |"]
    assert len(markers) == 1, "Expected exactly one authoritative publisher script"
    body = []
    for line in job[markers[0] + 1:]:
        if line.strip() and not line.startswith("            "):
            break
        body.append(line[12:])
    assert any(line.strip() for line in body), "Publisher script must not be empty"
    return "\n".join(body)


HARNESS = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];
const context = {eventName: input.event || 'push', ref: input.ref || 'refs/heads/p0-pit-context',
  sha: 'a'.repeat(40), runId: 100, runNumber: 42, repo: {owner:'owner',repo:'repo'}};
process.env.HOPE_CI_RUN_ATTEMPT = '2';
process.env.HOPE_CI_TEST_RESULT = input.result || 'success';
const github = {rest:{repos:{getBranch:async args => {
  calls.push(['branch',args]);return {data:{commit:{sha:input.stale ? 'b'.repeat(40):context.sha}}};
}},issues:{listComments:()=>{},updateComment:async args=>calls.push(['update',args]),
  createComment:async args=>calls.push(['create',args])}},
  paginate:async (method,args)=>{calls.push(['paginate',args]);return input.comments || []}};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
(async()=>{await new AsyncFunction('github','context',input.script)(github,context);
process.stdout.write(JSON.stringify(calls));})().catch(error=>{console.error(error);process.exit(1)});
"""


def publish(*, workflow_text=None, **case):
    node = shutil.which("node")
    assert node, "CI publisher tests require Node (installed in CI)"
    result = subprocess.run([node, "-e", HARNESS], input=json.dumps(dict(script=script(workflow_text), **case)),
                            text=True, capture_output=True, timeout=10, check=True)
    return json.loads(result.stdout)


def marker(login="github-actions[bot]", **fields):
    values = dict(sha="a"*40,run_number=41,run_attempt=1)
    values.update(fields)
    return dict(id=7,user=dict(login=login,type="Bot"),body="<!-- hope-ci-status -->\nHOPE_CI_STATUS_V1\n"+
                "\n".join(f"{key}={value}" for key,value in values.items()))


@pytest.mark.parametrize("case", [dict(ref="refs/heads/engineering/review"),
    dict(ref="refs/tags/release"),dict(event="pull_request"),
    dict(ref="refs/heads/engineering/review",event="workflow_dispatch")])
def test_unrelated_runs_cannot_read_or_write_authoritative_marker(case):
    assert publish(**case) == []


def test_authoritative_manual_dispatch_publishes_exact_head_result():
    calls = publish(event="workflow_dispatch", comments=[marker()])
    assert [call[0] for call in calls] == ["branch", "paginate", "update"]
    assert "event=workflow_dispatch" in calls[-1][1]["body"]


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_stale_authoritative_event_cannot_overwrite_current_marker(event):
    assert [call[0] for call in publish(stale=True, event=event)] == ["branch"]


@pytest.mark.parametrize("fields", [dict(run_number=43),dict(run_number=42,run_attempt=3)])
def test_same_sha_older_run_or_attempt_cannot_regress_marker(fields):
    assert [call[0] for call in publish(comments=[marker(**fields)])] == ["branch","paginate"]


def test_publisher_uses_paginated_exact_bot_identity_and_real_test_result():
    other = marker(login="other-bot")
    other["id"] = 999
    calls = publish(comments=[other,marker()],result="failure")
    assert [call[0] for call in calls] == ["branch","paginate","update"]
    assert calls[1][1]["per_page"] == 100
    update = calls[-1][1]
    assert update["comment_id"] == 7
    assert "test_result=failure" in update["body"]
    assert "run_attempt=2" in update["body"]
    assert "sha="+"a"*40 in update["body"]


def test_missing_marker_is_created_without_adopting_other_bot_comment():
    calls = publish(comments=[marker(login="other-bot")])
    assert calls[-1][0] == "create"
    assert calls[-1][1]["issue_number"] == 5


def test_workflow_job_is_scoped_to_authoritative_branch_even_on_failure():
    assert "(github.event_name == 'push' || github.event_name == 'workflow_dispatch')" in WORKFLOW.read_text()
    assert "github.ref == 'refs/heads/p0-pit-context'" in WORKFLOW.read_text()


def test_current_branch_evidence_replaces_unrelated_branch_marker():
    calls = publish(comments=[marker(sha="b"*40,run_number=999)])
    assert calls[-1][0] == "update"
    assert "sha="+"a"*40 in calls[-1][1]["body"]


@pytest.mark.parametrize("placement", ["before", "after", "next_step"])
def test_adjacent_workflow_code_cannot_enter_the_publisher(placement):
    text = WORKFLOW.read_text(encoding="utf-8")
    decoy = ("  unrelated:\n    steps:\n      - uses: actions/github-script@example\n"
             "        with:\n          script: |\n"
             "            throw new Error('UNRELATED_SCRIPT_EXECUTED');\n")
    if placement == "before":
        text = text.replace("  publish-ci-status:\n", decoy + "  publish-ci-status:\n", 1)
    elif placement == "after":
        text += "\n" + decoy
    else:
        text = text.replace("\n  private-preview-build:\n",
            "\n      - name: unrelated publisher follow-up\n"
            "        run: throw new Error('UNRELATED_STEP_EXECUTED');\n"
            "\n  private-preview-build:\n", 1)
    calls = publish(workflow_text=text, comments=[marker()], result="failure")
    assert [call[0] for call in calls] == ["branch", "paginate", "update"]
    assert calls[-1][1]["comment_id"] == 7
    assert "test_result=failure" in calls[-1][1]["body"]
    assert "sha=" + "a" * 40 in calls[-1][1]["body"]
