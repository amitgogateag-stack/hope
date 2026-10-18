"""Safe local observations; endpoint reachability never implies PAPER readiness."""
from datetime import UTC,datetime
import json

from hope.api.dashboard_server import _verified_operations, _verified_snapshot


def dashboard_readiness(snapshot,operations):
    now = datetime.now(UTC)
    result = dict(schema="hope.operational-readiness.v1",generated_at=now.isoformat(),server="RUNNING",
        paper_health="UNAVAILABLE",reason="HEALTH_MISSING",worker_report="UNKNOWN",snapshot="UNAVAILABLE",
        database="UNVERIFIED",reconciliation="UNVERIFIED",last_successful_poll_at=None,
        last_successful_paper_at=None,last_failure_at=None,last_failure_code=None,
        execution_authorized=False,paper_only=True,live_enabled=False)
    data = None
    try:
        raw,generated = _verified_snapshot(snapshot)
        age = (now-generated).total_seconds()
        result["snapshot"] = "CURRENT" if 0<=age<=3600 else "STALE" if age>3600 else "UNAVAILABLE"
        result["database"] = "OBSERVED_AT_SNAPSHOT" if result["snapshot"]=="CURRENT" else "UNVERIFIED"
        # Historical/future snapshots cannot establish a current HALT, claim
        # count or activity observation. They also cannot authorize execution.
        data = json.loads(raw) if result["snapshot"]=="CURRENT" else None
        if data and data["paper"]["environment_state"]=="HALTED":
            result.update(paper_health="HALTED",reason="DURABLE_HALT_OBSERVED")
        activity = (data or {}).get("projections",{}).get("paper_activity",{})
        if activity.get("status")=="AVAILABLE":
            result["last_successful_paper_at"] = activity.get("last_completed_at")
        if data and data["paper"].get("recovery",{}).get("claimed_with_effects",0)>0:
            result["reconciliation"] = "CLAIMS_REQUIRE_REVIEW"
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
    elif health["state"]=="HALTED" or (data and data["paper"]["environment_state"]=="HALTED"):
        result.update(paper_health="HALTED",reason="DURABLE_HALT_OBSERVED")
    elif health["state"]!="CURRENT":
        result["reason"] = "WORKER_"+health["state"]
    elif health["cycles_succeeded"]<1 or health["error_code"] is not None:
        result["reason"] = "WORKER_STATUS_INCONSISTENT"
    elif (now-datetime.fromisoformat(health["last_success_at"])).total_seconds()>300:
        result["reason"] = "POLL_NOT_CURRENT"
    elif result["snapshot"]!="CURRENT":
        result["reason"] = "SNAPSHOT_NOT_CURRENT"
    elif health["schema_compatibility"]!="COMPATIBLE" or health["deployed_commit"] is None:
        result["reason"] = "WORKER_IDENTITY_UNVERIFIED"
    elif result["reconciliation"]=="CLAIMS_REQUIRE_REVIEW":
        result["reason"] = "CLAIMS_REQUIRE_REVIEW"
    else:
        result.update(paper_health="READY",reason="CURRENT_OBSERVATIONS_ONLY")
    return result
