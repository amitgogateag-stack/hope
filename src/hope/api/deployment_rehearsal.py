"""Disposable adapters around the production activation/rollback state machine.

No systemctl, broker, installer, cloud API or production database connection.
Reports describe lab observations only and never confer execution authority.
"""
from __future__ import annotations
from datetime import UTC,datetime
from pathlib import Path
import tempfile

from hope.api import dashboard_activation as activation,operations_deployment as operations
from hope.api.runtime_integrity import attest_runtime,source_snapshot,safe_evidence,_require,_read

MARKER=b"hope.disposable-rehearsal.v1\n"


class RehearsalHost:
    def __init__(self,layout,*,database_probe,health_probe,source_verifier=operations.inspect_release):
        root=layout.root.resolve(strict=True)
        _require(root==layout.root and root.is_relative_to(Path(tempfile.gettempdir()).resolve(strict=True))
            and root!=Path(tempfile.gettempdir()).resolve(strict=True)
            and _read(root/".hope-rehearsal",64)==MARKER,"REHEARSAL_ROOT_REQUIRED")
        _require(layout.units==root/"units","REHEARSAL_UNITS_INVALID")
        self.layout=layout;self.database_probe=database_probe;self.health_probe=health_probe
        self.source_verifier=source_verifier;self.runtime_manifests={};self.calls=[];self.running=False

    def paper_safe(self):
        _require(_read(self.layout.root/".hope-rehearsal",64)==MARKER,"REHEARSAL_ROOT_CHANGED")

    def inspect(self,sha,*,mode=None):
        self.paper_safe();release=self.layout.root/"releases"/sha
        self.source_verifier(release,sha);source_snapshot(release)
        mode=mode or activation._runtime_mode(self.layout,sha)
        record=activation._record(self.layout);binding=record or {}
        if record and record["schema"]!="hope.dashboard-activation.v3" and record["phase"] in {"ROLLING_BACK","ROLLBACK_FAILED"}:
            binding=activation._backup(self.layout,record).get("runtime_binding",binding)
        expected=self.runtime_manifests.get(sha)
        if "runtime_manifest" in binding:
            if sha==binding["target"]:expected=binding["runtime_manifest"]
            elif sha==(binding["previous"] or binding["shared"]):expected=binding["previous_runtime_manifest"]
        evidence=attest_runtime(self.layout.root,sha,mode,expected_manifest=expected)
        _require(evidence["provenance_state"]=="VERIFIED","PACKAGE_PROVENANCE_REQUIRED")
        database=self.database_probe(sha)
        _require(database==dict(release_commit=sha,read_only=True,schema="COMPATIBLE"),"REHEARSAL_DATABASE_UNVERIFIED")
        self.runtime_manifests[sha]=evidence["manifest_sha256"]
        return evidence

    def preflight(self,target,shared,previous,*ci):
        self.inspect(previous or shared)
        self.inspect(target,mode=getattr(self,"target_runtime","shared"))
        return False

    def rollback_preflight(self,sha):self.inspect(sha)
    def stop(self):self.calls.append("stop");self.running=False
    def reload(self):self.calls.append("reload")
    def start(self,timer):self.calls.append("start");self.running=True

    def verify(self,sha,*,managed,**kwargs):
        evidence=self.inspect(sha)
        _require(self.running and self.health_probe(sha)==dict(release_commit=sha,
            worker="CURRENT",snapshot="CURRENT",reconciliation="CLEAR",live_enabled=False),"REHEARSAL_HEALTH_BLOCKED")
        return evidence


def rehearse_upgrade(layout,host,*,target,shared,ci_run,ci_attempt,runtime="release"):
    """A lab-only end-to-end attempt, using real journals/atomic pointers/leases."""
    activation.activate(layout,host,target=target,shared=shared,ci_run=ci_run,ci_attempt=ci_attempt,runtime=runtime)
    evidence=host.inspect(target)
    result=dict(schema="hope.deployment-rehearsal.v1",scope="DISPOSABLE_LAB_ONLY",state="VERIFIED",
        release_commit=target,previous_commit=shared,generated_at=datetime.now(UTC).isoformat(),
        runtime_integrity=safe_evidence(evidence),execution_authorized=False,host_changes=False)
    activation._save(layout.root/"rehearsal-result.json",result)
    return result
