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
        operations.inspect_release(release,sha)
        _require(source_snapshot(release)==before,"SOURCE_CHANGED")
    return environment_path(root,sha,mode)/"bin/python",evidence


def handoff(release,arguments,mode,*,execute=os.execve):
    python,evidence=authenticate_control(release,mode)
    bootstrap=Path(release)/"deploy/operations/dashboard_release.py"
    environment={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LANG":"C","LC_ALL":"C"}
    if "GH_TOKEN" in os.environ:environment["GH_TOKEN"]=os.environ["GH_TOKEN"]
    return execute(str(python),[str(python),"-I","-B","-X",CACHE_PREFIX,str(bootstrap),
        "--control-runtime",mode,"--control-manifest",evidence["manifest_sha256"],*arguments],environment)


def main(argv=None,*,release):
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument("--control-runtime",choices=("shared","release"),default="shared")
    parser.add_argument("--control-manifest")
    args,forwarded=parser.parse_known_args(argv)
    try:
        _require(sys.flags.isolated,"BOOTSTRAP_ISOLATION_REQUIRED")
        if args.control_manifest is None:
            _require(sys.flags.no_site,"BOOTSTRAP_NO_SITE_REQUIRED")
            return handoff(release,forwarded,args.control_runtime)
        python,_=authenticate_control(release,args.control_runtime,expected_manifest=args.control_manifest)
        verify_interpreter(python)
        _require(sys.version_info[:2]==(3,13),"BOOTSTRAP_PYTHON_VERSION_INVALID")
        from hope.api.dashboard_activation import main as activate_main
        return activate_main(forwarded)
    except Exception:
        parser.exit(2,"DASHBOARD_BOOTSTRAP_BLOCKED REVIEW_SOURCE_AND_RUNTIME_MANIFEST\n")
