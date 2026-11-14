"""Safe local observations; endpoint reachability never implies PAPER readiness."""
from datetime import UTC,datetime
import json
import re

from hope.api.dashboard_server import _verified_operations, _verified_snapshot
from hope.api.snapshot_contract import recovery_state
from hope.api.runtime_integrity import safe_evidence


def dashboard_readiness(snapshot,operations,*,expected_commit=None,expected_paper_commit=None,
                        runtime_evidence=None,snapshot_authenticated=False):
    if expected_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise ValueError("HOPE_DASHBOARD_EXPECTED_COMMIT_INVALID")
    if expected_paper_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", expected_paper_commit):
        raise ValueError("HOPE_PAPER_EXPECTED_COMMIT_INVALID")
    if type(snapshot_authenticated) is not bool:
        raise ValueError("HOPE_SNAPSHOT_AUTHENTICATION_INVALID")
    now = datetime.now(UTC)
    result = dict(schema="hope.operational-readiness.v1",generated_at=now.isoformat(),server="RUNNING",
        paper_health="UNAVAILABLE",reason="HEALTH_MISSING",worker_report="UNKNOWN",snapshot="UNAVAILABLE",
        database="UNVERIFIED",reconciliation="UNVERIFIED",last_successful_poll_at=None,
        last_successful_paper_at=None,last_failure_at=None,last_failure_code=None,
        execution_authorized=False,paper_only=True,live_enabled=False)
    runtime=safe_evidence(runtime_evidence)
    result["runtime_integrity"]=runtime
    data = None
    snapshot_identity = expected_commit is None
    try:
        raw,generated = _verified_snapshot(snapshot)
        age = (now-generated).total_seconds()
        result["snapshot"] = "CURRENT" if 0<=age<=3600 else "STALE" if age>3600 else "UNAVAILABLE"
        candidate = json.loads(raw) if result["snapshot"]=="CURRENT" else None
        snapshot_identity = (expected_commit is None or
            (candidate is not None and candidate.get("deployed_commit") == expected_commit))
        if snapshot_identity and snapshot_authenticated:
            result["database"] = "OBSERVED_AT_SNAPSHOT" if result["snapshot"]=="CURRENT" else "UNVERIFIED"
        elif result["snapshot"] == "CURRENT":
            result["reason"] = "SNAPSHOT_IDENTITY_UNVERIFIED"
        # Historical/future snapshots cannot establish a current HALT, claim
        # count or activity observation. They also cannot authorize execution.
        # Shape, time and release labels inside a same-UID-writable snapshot are
        # diagnostics, not publisher authority. Restrictive HALT remains safe
        # to surface; positive RUNNING and recovery claims require independent
        # authentication of the snapshot bytes.
        diagnostic = candidate if snapshot_identity else None
        data = diagnostic if snapshot_authenticated else None
        if diagnostic and diagnostic["paper"]["environment_state"]=="HALTED":
            result.update(paper_health="HALTED",reason="DURABLE_HALT_OBSERVED")
        activity = (data or {}).get("projections",{}).get("paper_activity",{})
        if activity.get("status")=="AVAILABLE":
            result["last_successful_paper_at"] = activity.get("last_completed_at")
        if diagnostic:result["reconciliation"] = recovery_state(diagnostic["paper"])
    except (OSError,ValueError,TypeError,UnicodeError):
        pass
    try:
        health = json.loads(_verified_operations(operations))
    except FileNotFoundError:
        return result
    except PermissionError:
        if result["paper_health"]!="HALTED":result["reason"] = "HEALTH_UNREADABLE"
        return result
    except (OSError,ValueError,TypeError,UnicodeError):
        if result["paper_health"]!="HALTED":result["reason"] = "HEALTH_INVALID"
        return result
    # The file validators allow limited transport clock skew; a future-dated
    # observation still must never be promoted into current readiness.
    if any(datetime.fromisoformat(health[key])>now for key in
            ("generated_at","last_success_at","last_failure_at") if health.get(key) is not None):
        if result["paper_health"]!="HALTED":result["reason"]="HEALTH_FROM_FUTURE"
        return result
    result.update(worker_report=health["state"],last_successful_poll_at=health["last_success_at"],
        last_failure_at=health.get("last_failure_at"),last_failure_code=health.get("last_failure_code"))
    generated = datetime.fromisoformat(health["generated_at"])
    if result["paper_health"]=="HALTED":
        pass  # A current durable snapshot HALT survives missing/stale health.
    elif (now-generated).total_seconds()>300:
        result["reason"] = "HEALTH_STALE"
    elif expected_paper_commit is not None and health["deployed_commit"] != expected_paper_commit:
        result["reason"] = "WORKER_IDENTITY_UNVERIFIED"
    elif health["state"]=="HALTED" or (data and data["paper"]["environment_state"]=="HALTED"):
        result.update(paper_health="HALTED",reason="DURABLE_HALT_OBSERVED")
    elif health["state"]!="CURRENT":
        result["reason"] = "WORKER_"+health["state"]
    elif health["cycles_succeeded"]<1 or health["error_code"] is not None:
        result["reason"] = "WORKER_STATUS_INCONSISTENT"
    elif (now-datetime.fromisoformat(health["last_success_at"])).total_seconds()>300:
        result["reason"] = "POLL_NOT_CURRENT"
    elif not snapshot_identity:
        result["reason"] = "SNAPSHOT_IDENTITY_UNVERIFIED"
    elif result["snapshot"]!="CURRENT":
        result["reason"] = "SNAPSHOT_NOT_CURRENT"
    elif health["schema_compatibility"]!="COMPATIBLE" or health["deployed_commit"] is None:
        result["reason"] = "WORKER_IDENTITY_UNVERIFIED"
    elif result["reconciliation"] in {"CLAIMS_REQUIRE_REVIEW","RECOVERY_REQUIRED"}:
        result["reason"] = result["reconciliation"]
    elif (runtime["state"]!="VERIFIED"
            or (expected_commit is not None and runtime["release_commit"]!=expected_commit)):
        result["reason"]="RUNTIME_"+(runtime["state"] if runtime["state"]!="VERIFIED" else "MISMATCH")
    elif runtime["provenance_state"]!="VERIFIED":
        result["reason"]="PACKAGE_PROVENANCE_UNVERIFIED"
    elif runtime["build_state"]!="VERIFIED":
        result["reason"]="RELEASE_BUILD_UNVERIFIED"
    elif health.get("runtime_gate")!="VERIFIED":
        result["reason"]="PAPER_RUNTIME_UNVERIFIED"
    elif not snapshot_authenticated:
        result["reason"] = "SNAPSHOT_PUBLISHER_UNVERIFIED"
    else:
        result.update(paper_health="READY",reason="CURRENT_OBSERVATIONS_ONLY")
    return result
