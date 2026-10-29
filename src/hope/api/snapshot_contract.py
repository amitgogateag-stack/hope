"""One pure bounded snapshot contract for local serving and host attestation."""
from datetime import datetime, timezone
import json
from re import fullmatch

from hope.api.dashboard_projection import verified_projections


def recovery_state(paper):
    """A stale claim requires investigation even when no effect was persisted."""
    recovery=paper.get("recovery",{})
    if recovery.get("claims_older_than_one_hour",0)>0:return "RECOVERY_REQUIRED"
    if recovery.get("claimed_with_effects",0)>0:return "CLAIMS_REQUIRE_REVIEW"
    return "UNVERIFIED"


def snapshot_evidence(payload,*,expected_commit,observed_at,status_timestamp):
    """Authenticate persisted identity, one generation, HALT and recovery state.

    No activity count, including zero jobs/NO_TRADE, authorizes execution.
    """
    safe,generated=verified_snapshot_payload(payload,now=observed_at)
    data=json.loads(safe);paper=data["paper"];blockers=[]
    if data.get("deployed_commit")!=expected_commit:blockers.append("SNAPSHOT_RELEASE_MISMATCH")
    age=(observed_at-generated).total_seconds()
    if age<0:blockers.append("SNAPSHOT_FROM_FUTURE")
    elif age>900:blockers.append("SNAPSHOT_STALE")
    try:
        status_generated=datetime.fromisoformat(status_timestamp.replace("Z","+00:00"))
        if status_generated.tzinfo is None:raise ValueError
    except (ValueError,TypeError,AttributeError):blockers.append("SNAPSHOT_STATUS_GENERATION_INVALID")
    else:
        if status_generated!=generated:blockers.append("SNAPSHOT_STATUS_GENERATION_MISMATCH")
    if paper["environment_state"]=="HALTED":blockers.append("PAPER_DURABLE_HALT_OBSERVED")
    if "recovery" not in paper:blockers.append("SNAPSHOT_RECOVERY_UNVERIFIED")
    state=recovery_state(paper)
    if state!="UNVERIFIED":blockers.append("PAPER_"+state)
    return dict(deployed_commit=data.get("deployed_commit"),generated_at=generated.isoformat(),
        environment_state=paper["environment_state"],reconciliation=state,
        recovery=paper.get("recovery"),job_runs=paper["job_runs"]),blockers


def strict_json(payload):
    def object_pairs(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError("HOPE_JSON_DUPLICATE_KEY")
            value[key]=item
        return value
    def nonfinite(value):raise ValueError("HOPE_JSON_NONFINITE")
    try:value=json.loads(payload,object_pairs_hook=object_pairs,parse_constant=nonfinite)
    except RecursionError as exc:raise ValueError("HOPE_JSON_DEPTH_INVALID") from exc
    pending=[(value,0)]
    while pending:
        item,depth=pending.pop()
        if depth>32:raise ValueError("HOPE_JSON_DEPTH_INVALID")
        if isinstance(item,dict):pending.extend((child,depth+1) for child in item.values())
        elif isinstance(item,list):pending.extend((child,depth+1) for child in item)
    return value


def verified_snapshot_payload(payload: bytes, *, expected_commit: str | None = None, now: datetime | None = None) -> tuple[bytes, datetime]:
    """Reject malformed, partial, future-dated or oversized snapshot files."""
    if not isinstance(payload, bytes) or len(payload)>32768:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_SIZE_INVALID")
    data = strict_json(payload)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1 or data.get("source") != "hope_postgresql":
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INVALID")
    commit = data.get("deployed_commit")
    if commit is not None and (not isinstance(commit, str) or fullmatch(r"[0-9a-f]{40}", commit) is None):
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_COMMIT_INVALID")
    if expected_commit is not None and commit != expected_commit:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_COMMIT_MISMATCH")
    research = data.get("research")
    paper = data.get("paper")
    if not isinstance(research, dict) or not isinstance(paper, dict):
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INCOMPLETE")
    counts = paper.get("job_runs")
    valid_count = lambda n: type(n) is int and 0 <= n <= 9007199254740991
    if not valid_count(research.get("run_count")) or paper.get("environment_state") not in {"RUNNING", "HALTED"}:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INCOMPLETE")
    if not isinstance(counts, dict) or not all(
        valid_count(counts.get(key)) for key in ("total", "claimed", "succeeded", "failed")
    ):
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INCOMPLETE")
    if counts["total"] != counts["claimed"] + counts["succeeded"] + counts["failed"]:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_INCONSISTENT")
    recovery = paper.get("recovery")
    recovery_fields = (
        "claimed_with_effects", "claimed_without_effects",
        "claims_older_than_one_hour", "reconciliation_receipts",
    )
    if "recovery" in paper:
        if not isinstance(recovery, dict) or not all(valid_count(recovery.get(k)) for k in recovery_fields):
            raise ValueError("HOPE_DASHBOARD_RECOVERY_COUNTS_INVALID")
        if (recovery["claimed_with_effects"] + recovery["claimed_without_effects"] != counts["claimed"]
                or recovery["claims_older_than_one_hour"] > counts["claimed"]
                or recovery["reconciliation_receipts"] > counts["succeeded"]):
            raise ValueError("HOPE_DASHBOARD_RECOVERY_COUNTS_INCONSISTENT")
    stamp = data.get("generated_at")
    if not isinstance(stamp, str):
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_TIMESTAMP_INVALID")
    try:
        generated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_TIMESTAMP_INVALID") from exc
    if generated.tzinfo is None:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_TIMESTAMP_INVALID")
    if (generated - (now or datetime.now(timezone.utc))).total_seconds() > 300:
        raise ValueError("HOPE_DASHBOARD_SNAPSHOT_FROM_FUTURE")
    # The local snapshot file is an untrusted input boundary. Never serve
    # arbitrary top-level or nested fields that could contain identifiers,
    # credentials, fills, positions, or other sensitive database material.
    safe = {
        "schema_version": 1,
        "source": "hope_postgresql",
        "generated_at": generated.isoformat(),
        "research": {"run_count": research["run_count"]},
        "paper": {
            "environment_state": paper["environment_state"],
            "job_runs": {
                key: counts[key]
                for key in ("total", "claimed", "succeeded", "failed")
            },
        },
    }
    if commit is not None:
        safe["deployed_commit"] = commit
    if recovery is not None:
        safe["paper"]["recovery"] = {key: recovery[key] for key in recovery_fields}
    if "projections" in data:
        safe["projections"] = verified_projections(data["projections"], generated)
    return json.dumps(safe, separators=(",", ":")).encode("utf-8"), generated

