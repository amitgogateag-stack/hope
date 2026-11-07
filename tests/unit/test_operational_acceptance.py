from datetime import datetime,timezone,timedelta
import pytest
from hope.api.release_acceptance import acceptance_observation
from test_m3_dashboard_readiness import verified_runtime


@pytest.mark.parametrize("fault",[None,"expired","future","database","scheduler","market_data","reconciliation","risk","live_enabled","process_identity","release_commit","missing","build"])
def test_required_independent_observations_and_expiry(fault):
    runtime=verified_runtime();now=datetime.now(timezone.utc);sha="a"*40
    observed=dict(release_commit=sha,generated_at=now.isoformat(),process_identity="d"*64,database="COMPATIBLE",
        scheduler="CURRENT",market_data="VERIFIED",reconciliation="CLEAR",risk="CLEAR",live_enabled=False)
    if fault in {"expired","future"}:observed["generated_at"]=(now+timedelta(seconds=1 if fault=="future" else -301)).isoformat()
    elif fault=="missing":observed.pop("database")
    elif fault=="build":runtime.update(build_state="UNVERIFIED",build_sha256=None,builder_id=None)
    elif fault is not None:observed[fault]=True if fault=="live_enabled" else "UNVERIFIED"
    result=acceptance_observation(runtime,observed,expected_sha=sha,now=now)
    assert result["state"]==("VERIFIED" if fault is None else "BLOCKED")
    assert result["scope"]=="DISPOSABLE_LAB_ONLY" and result["execution_authorized"] is False
