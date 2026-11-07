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
def paper_operational_guard(release,*,root=Path("/opt/hope-operations")):
    release=Path(release);root=Path(root)
    entered=False
    try:
        sha=_sha(os.environ.get("HOPE_DEPLOYED_COMMIT"))
        _require(os.environ.get("HOPE_LIVE_ENABLED")=="false","LIVE_DISABLED_REQUIRED")
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
