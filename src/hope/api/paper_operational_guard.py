"""Read-only deployment/runtime lease for the production PAPER supervisor."""
from __future__ import annotations
from contextlib import contextmanager
import os
from pathlib import Path
import sys

from hope.api import operations_deployment as operations
from hope.api.runtime_integrity import attest_runtime,source_snapshot,environment_path,_sha,_require
from hope.api.runtime_identity import verify_interpreter


@contextmanager
def paper_operational_guard(release,*,root=Path("/opt/hope-operations"),deployment_receipt=None,factory_spec=None,host_probe=None,controller_directory=None):
    release=Path(release);root=Path(root)
    entered=False
    try:
        sha=_sha(os.environ.get("HOPE_DEPLOYED_COMMIT"))
        _require(os.environ.get("HOPE_LIVE_ENABLED")=="false","LIVE_DISABLED_REQUIRED")
        _require(controller_directory is None or deployment_receipt is not None,"CONTROLLER_RECEIPT_REQUIRED")
        _require(release==root/"releases"/sha and release.resolve(strict=True)==release,"PAPER_RELEASE_INVALID")
        mode="shared" if Path(sys.prefix)==root/"venv" else "release"
        python=environment_path(root,sha,mode)/"bin/python"
        verify_interpreter(python)
        with operations._activation_guard(root,shared=True):
            def observe():
                from hope.api.dashboard_activation import Layout,_record,_pointer
                _require(_pointer(Layout(root=root),"current")==sha,"PAPER_POINTER_MISMATCH")
                record=_record(Layout(root=root))
                _require(record is None or (record["phase"]=="COMMITTED" and record["shared"]==sha),"PAPER_ACTIVATION_INCOMPLETE")
                operations.inspect_release(release,sha);source=source_snapshot(release)
                evidence=attest_runtime(root,sha,mode,release=release)
                _require(evidence.get("provenance_state")=="VERIFIED","PACKAGE_PROVENANCE_REQUIRED")
                _require(evidence.get("build_state")=="VERIFIED","RELEASE_BUILD_REQUIRED")
                if deployment_receipt is not None:
                    from hope.api.deployment_contract import inspect_deployment
                    contract=inspect_deployment(root,sha,expected_receipt=deployment_receipt,expected_factory=factory_spec,
                        **({"host_probe":host_probe} if host_probe is not None else {}))
                    _require(contract['state']=='VERIFIED','DEPLOYMENT_CONTRACT_REQUIRED')
                    if controller_directory is not None:
                        from hope.api.paper_controller import observation,plan
                        controller=observation(controller_directory,root,inspector=lambda *a,**k:contract,
                            expected_plan=plan(sha,deployment_receipt,factory_spec))
                        _require(controller['state']=='VERIFIED' and controller['release_commit']==sha
                            and controller['receipt_sha256']==deployment_receipt,'CONTROLLER_VERIFICATION_REQUIRED')
                return evidence,source
            original=observe()
            def check():
                try:_require(observe()==original,"PAPER_RUNTIME_CHANGED")
                except Exception:raise RuntimeError("PAPER_RUNTIME_UNVERIFIED") from None
            entered=True
            yield check
            check()
    except Exception:
        if entered:raise
        raise RuntimeError("PAPER_RUNTIME_UNVERIFIED") from None
