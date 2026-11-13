"""Read-only expiring lab acceptance projection, never a READY authority."""
from datetime import datetime,timezone
import re
from hope.api.snapshot_contract import strict_json
from hope.infrastructure.durable_file import authenticated_bounded_regular_read

ACTIONS={"REVIEW_RELEASE_BUILD_AND_RUNTIME","REVIEW_MISSING_EVIDENCE","REFRESH_EXPIRED_EVIDENCE","REVIEW_DEPLOYMENT_IDENTITY",
    "REVIEW_PROCESS_IDENTITY","REVIEW_DATABASE","REVIEW_SCHEDULER","REFRESH_VERIFIED_MARKET_INPUTS",
    "RECONCILE_DURABLE_CLAIMS","REVIEW_DURABLE_HALT","LIVE_MUST_REMAIN_DISABLED"}


def read_acceptance(path, *, expected_sha256=None):
    missing=dict(schema="hope.acceptance-observation.v1",scope="DISPOSABLE_LAB_ONLY",state="UNVERIFIED",
        release_commit=None,generated_at=None,blockers=[],execution_authorized=False)
    # A local result file is not its own authority.  Callers without an
    # independently authenticated digest may expose only the fail-closed
    # projection; in particular, the dashboard must not promote same-host
    # bytes into a VERIFIED acceptance claim.
    if expected_sha256 is None:return missing
    try:
        value=strict_json(authenticated_bounded_regular_read(
            path,8192,expected_sha256=expected_sha256))
        if (not isinstance(value,dict) or value.get("schema")!="hope.operational-acceptance.v1"
                or set(value)!={"schema","scope","state","release_commit","generated_at","blockers","execution_authorized"}
                or value.get("scope")!="DISPOSABLE_LAB_ONLY" or value.get("execution_authorized") is not False
                or value.get("state") not in {"VERIFIED","BLOCKED"}):return missing
        blockers=value["blockers"]
        if (not isinstance(blockers,list) or len(blockers)>len(ACTIONS) or any(not isinstance(v,str) or v not in ACTIONS for v in blockers)
                or (value["state"]=="VERIFIED")!= (not blockers)
                or not isinstance(value["release_commit"],str) or not re.fullmatch("[0-9a-f]{40}",value["release_commit"])):return missing
        stamp=datetime.fromisoformat(value["generated_at"])
        if stamp.tzinfo is None:return missing
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
        if age<0:return missing
        return dict(missing,state="STALE" if age>300 else value["state"],release_commit=value["release_commit"],
            generated_at=stamp.isoformat(),blockers=sorted(set(blockers)))
    except (OSError,ValueError,TypeError,KeyError):return missing
