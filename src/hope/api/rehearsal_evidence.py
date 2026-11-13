"""Bounded historical lab observations; never an operational readiness input."""
from datetime import datetime,timezone
import re

from hope.api.runtime_integrity import safe_evidence
from hope.api.snapshot_contract import strict_json
from hope.infrastructure.durable_file import authenticated_bounded_regular_read


def read_rehearsal(path, *, expected_sha256=None):
    missing=dict(schema="hope.rehearsal-observation.v1",state="UNVERIFIED",scope="DISPOSABLE_LAB_ONLY",
        release_commit=None,generated_at=None,execution_authorized=False)
    # Historical lab output is not its own authority.  Until a separately
    # trusted publisher supplies its digest, expose only the fail-closed view.
    if expected_sha256 is None:return missing
    try:
        value=strict_json(authenticated_bounded_regular_read(
            path,8192,expected_sha256=expected_sha256))
        if (not isinstance(value,dict) or value.get("schema")!="hope.deployment-rehearsal.v1"
                or value.get("scope")!="DISPOSABLE_LAB_ONLY" or value.get("state")!="VERIFIED"
                or value.get("execution_authorized") is not False or value.get("host_changes") is not False):return missing
        commit=value["release_commit"];generated=datetime.fromisoformat(value["generated_at"])
        runtime=safe_evidence(value["runtime_integrity"])
        if (not isinstance(commit,str) or not re.fullmatch("[0-9a-f]{40}",commit)
                or runtime["release_commit"]!=commit or runtime["state"]!="VERIFIED"
                or runtime["provenance_state"]!="VERIFIED" or generated.tzinfo is None):return missing
        age=(datetime.now(timezone.utc)-generated).total_seconds()
        if age<0:return missing
        return dict(missing,state="VERIFIED" if age<=86400 else "STALE",release_commit=commit,generated_at=generated.isoformat())
    except (OSError,ValueError,TypeError,KeyError,RuntimeError):return missing
