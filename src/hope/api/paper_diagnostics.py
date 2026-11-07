"""Fixed operator actions from validated observations; no execution authority."""
def paper_diagnostics(readiness,health):
    code=(health or {}).get("error_code");recovery=readiness.get("reconciliation")
    action="REVIEW_HEALTH_EVIDENCE";scheduler="UNVERIFIED";market="UNVERIFIED";state="UNVERIFIED"
    if readiness["paper_health"]=="HALTED":state="HALTED";action="REVIEW_DURABLE_HALT"
    elif recovery in {"RECOVERY_REQUIRED","CLAIMS_REQUIRE_REVIEW"} or code=="PAPER_JOB_INCOMPLETE_PRIOR_CLAIM":
        state="RECOVERY_REQUIRED";action="RECONCILE_DURABLE_CLAIMS"
    elif code and code.startswith("PAPER_SCHEDULER_"):
        state="STALE" if code=="PAPER_SCHEDULER_RUN_STALE" else "BLOCKED";scheduler=state;action="REVIEW_SCHEDULER_AND_CLOCK"
    elif code in {"PAPER_MARKET_DATA_STALE","PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT"}:
        state="BLOCKED";market="STALE" if code=="PAPER_MARKET_DATA_STALE" else "UNVERIFIED";action="REFRESH_VERIFIED_MARKET_INPUTS"
    elif readiness["reason"] in {"RUNTIME_UNVERIFIED","RUNTIME_BLOCKED","RUNTIME_MISMATCH","PACKAGE_PROVENANCE_UNVERIFIED","PAPER_RUNTIME_UNVERIFIED"}:
        state="BLOCKED";action="REVIEW_DEPLOYMENT_AND_PROVENANCE"
    elif readiness["paper_health"]=="READY":state="VERIFIED";scheduler="VERIFIED";action="NONE"
    elif readiness["reason"] in {"HEALTH_STALE","SNAPSHOT_NOT_CURRENT","POLL_NOT_CURRENT"}:state="STALE"
    return dict(schema="hope.paper-diagnostics.v1",state=state,scheduler=scheduler,market_data_freshness=market,
        last_successful_cycle=readiness.get("last_successful_poll_at"),operator_action=action,
        execution_authorized=False,paper_only=True,live_enabled=False)
