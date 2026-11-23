"""OS-Python bootstrap: authenticate a venv before executing any of its code."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from hope.api import operations_deployment as operations
from hope.api.runtime_integrity import attest_runtime,environment_path,source_snapshot,_sha,_require
from hope.api.runtime_identity import verify_interpreter

CACHE_PREFIX="pycache_prefix=/nonexistent/hope-runtime-bootstrap"


def authenticate_control(release,mode,*,expected_manifest=None):
    release=Path(release);sha=_sha(release.name);root=release.parent.parent
    _require(release.parent.name=="releases" and release.resolve(strict=True)==release,"BOOTSTRAP_RELEASE_INVALID")
    with operations._activation_guard(root,shared=True):
        operations.inspect_release(release,sha)
        before=source_snapshot(release)
        evidence=attest_runtime(root,sha,mode,expected_manifest=expected_manifest,release=release)
        _require(evidence.get("provenance_state")=="VERIFIED","PACKAGE_PROVENANCE_REQUIRED")
        operations.inspect_release(release,sha)
        _require(source_snapshot(release)==before,"SOURCE_CHANGED")
    return environment_path(root,sha,mode)/"bin/python",evidence


def handoff(release,arguments,mode,*,observer_refresh=False,execute=os.execve):
    python,evidence=authenticate_control(release,mode)
    if observer_refresh:_require(evidence.get("build_state")=="VERIFIED","RELEASE_BUILD_REQUIRED")
    bootstrap=Path(release)/"deploy/operations/dashboard_release.py"
    environment={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LANG":"C","LC_ALL":"C"}
    credential="HOPE_DATABASE_URL" if observer_refresh else "GH_TOKEN"
    if credential in os.environ:environment[credential]=os.environ[credential]
    dispatch=["--observer-refresh"] if observer_refresh else []
    return execute(str(python),[str(python),"-I","-B","-X",CACHE_PREFIX,str(bootstrap),
        "--control-runtime",mode,"--control-manifest",evidence["manifest_sha256"],*dispatch,*arguments],environment)


def _observer_refresh(release,arguments):
    # The fixed one-shot command authenticates again at every timer invocation.
    # A long-running loop would retain an old runtime/PAPER-isolation decision.
    commits=[value for value in arguments if value=="--deployed-commit" or value.startswith("--deployed-commit=")]
    _require(len(commits)==1 and not any(value=="--interval-seconds" or value.startswith("--interval-seconds=")
        for value in arguments),"OBSERVER_ARGUMENTS_INVALID")
    parser=argparse.ArgumentParser(add_help=False,allow_abbrev=False)
    parser.add_argument("--deployed-commit",required=True)
    args,_=parser.parse_known_args(arguments)
    _require(args.deployed_commit==Path(release).name,"OBSERVER_RELEASE_MISMATCH")
    from hope.api.dashboard_activation import Host,Layout
    Host(Layout(root=Path(release).parent.parent)).paper_safe()
    # No database module is imported or dispatched before the preceding gates.
    from hope.api.dashboard_snapshot_refresh import main as refresh_main
    return refresh_main(arguments)


def main(argv=None,*,release):
    parser=argparse.ArgumentParser(add_help=False,allow_abbrev=False)
    parser.add_argument("--control-runtime",choices=("shared","release"),default="shared")
    parser.add_argument("--control-manifest")
    parser.add_argument("--observer-refresh",action="store_true")
    args,forwarded=parser.parse_known_args(argv)
    try:
        _require(sys.flags.isolated,"BOOTSTRAP_ISOLATION_REQUIRED")
        if args.control_manifest is None:
            _require(sys.flags.no_site,"BOOTSTRAP_NO_SITE_REQUIRED")
            if args.observer_refresh:
                return handoff(release,forwarded,args.control_runtime,observer_refresh=True)
            return handoff(release,forwarded,args.control_runtime)
        python,evidence=authenticate_control(release,args.control_runtime,expected_manifest=args.control_manifest)
        verify_interpreter(python)
        _require(sys.version_info[:2]==(3,13),"BOOTSTRAP_PYTHON_VERSION_INVALID")
        if args.observer_refresh:
            _require(evidence.get("build_state")=="VERIFIED","RELEASE_BUILD_REQUIRED")
            return _observer_refresh(release,forwarded)
        from hope.api.dashboard_activation import main as activate_main
        return activate_main(forwarded)
    except Exception:
        parser.exit(2,"DASHBOARD_BOOTSTRAP_BLOCKED REVIEW_SOURCE_AND_RUNTIME_MANIFEST\n")
