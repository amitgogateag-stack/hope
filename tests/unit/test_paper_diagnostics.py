import pytest
from hope.api.paper_diagnostics import paper_diagnostics


@pytest.mark.parametrize("health,ready,state,action",[
    ({},{},"UNVERIFIED","REVIEW_HEALTH_EVIDENCE"),
    ({},{"paper_health":"HALTED"},"HALTED","REVIEW_DURABLE_HALT"),
    ({},{"reconciliation":"RECOVERY_REQUIRED"},"RECOVERY_REQUIRED","RECONCILE_DURABLE_CLAIMS"),
    ({"error_code":"PAPER_JOB_INCOMPLETE_PRIOR_CLAIM"},{},"RECOVERY_REQUIRED","RECONCILE_DURABLE_CLAIMS"),
    ({"error_code":"PAPER_SCHEDULER_RUN_STALE"},{},"STALE","REVIEW_SCHEDULER_AND_CLOCK"),
    ({"error_code":"PAPER_SCHEDULER_CLOCK_REGRESSION"},{},"BLOCKED","REVIEW_SCHEDULER_AND_CLOCK"),
    ({"error_code":"PAPER_MARKET_DATA_STALE"},{},"BLOCKED","REFRESH_VERIFIED_MARKET_INPUTS"),
    ({"error_code":"PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT"},{},"BLOCKED","REFRESH_VERIFIED_MARKET_INPUTS"),
    ({},{"reason":"PACKAGE_PROVENANCE_UNVERIFIED"},"BLOCKED","REVIEW_DEPLOYMENT_AND_PROVENANCE"),
    ({},{"paper_health":"READY"},"VERIFIED","NONE"),
    ({},{"reason":"HEALTH_STALE"},"STALE","REVIEW_HEALTH_EVIDENCE"),
])
def test_operator_actions_are_explicit_and_never_authorize_execution(health,ready,state,action):
    readiness=dict(paper_health="UNAVAILABLE",reason="HEALTH_MISSING",reconciliation="UNVERIFIED",**{})
    readiness.update(ready)
    result=paper_diagnostics(readiness,health)
    assert result["state"]==state and result["operator_action"]==action
    assert result["execution_authorized"] is False and result["live_enabled"] is False
    if state=="VERIFIED":assert result["market_data_freshness"]=="UNVERIFIED"
