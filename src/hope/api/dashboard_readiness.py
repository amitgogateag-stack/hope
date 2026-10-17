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
        data = json.loads(raw)
        result["snapshot"] = "CURRENT" if (now-generated).total_seconds()<=3600 else "STALE"
        result["database"] = "OBSERVED_AT_SNAPSHOT" if result["snapshot"]=="CURRENT" else "UNVERIFIED"
        if result["snapshot"]=="CURRENT" and data["paper"]["environment_state"]=="HALTED":
            result.update(paper_health="HALTED",reason="DURABLE_HALT_OBSERVED")
        activity = data.get("projections",{}).get("paper_activity",{})
        if activity.get("status")=="AVAILABLE":
            result["last_successful_paper_at"] = activity.get("last_completed_at")
        if data["paper"].get("recovery",{}).get("claimed_with_effects",0)>0:
            result["reconciliation"] = "CLAIMS_REQUIRE_REVIEW"
    except (OSError,ValueError,TypeError,UnicodeError):
        pass
    try:
        health = json.loads(_verified_operations(operations))
    except FileNotFoundError:
        return result
    except PermissionError:
        result["reason"] = "HEALTH_UNREADABLE"
        return result
    except (OSError,ValueError,TypeError,UnicodeError):
        result["reason"] = "HEALTH_INVALID"
        return result
    result.update(worker_report=health["state"],last_successful_poll_at=health["last_success_at"],
        last_failure_at=health.get("last_failure_at"),last_failure_code=health.get("last_failure_code"))
    generated = datetime.fromisoformat(health["generated_at"])
    if (now-generated).total_seconds()>300:
        result["reason"] = "HEALTH_STALE"
    elif health["state"]=="HALTED" or (data and data["paper"]["environment_state"]=="HALTED"):
        result.update(paper_health="HALTED",reason="DURABLE_HALT_OBSERVED")
    elif health["state"]!="CURRENT":
        result["reason"] = "WORKER_"+health["state"]
    elif result["snapshot"]!="CURRENT":
        result["reason"] = "SNAPSHOT_NOT_CURRENT"
    elif health["schema_compatibility"]!="COMPATIBLE" or health["deployed_commit"] is None:
        result["reason"] = "WORKER_IDENTITY_UNVERIFIED"
    elif result["reconciliation"]=="CLAIMS_REQUIRE_REVIEW":
        result["reason"] = "CLAIMS_REQUIRE_REVIEW"
    else:
        result.update(paper_health="READY",reason="CURRENT_OBSERVATIONS_ONLY")
    return result
