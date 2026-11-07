"""OS trust-anchor launch for PAPER, including its database preflight."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys

from hope.api.runtime_bootstrap import authenticate_control,CACHE_PREFIX
from hope.api.runtime_identity import verify_interpreter
from hope.api.runtime_integrity import _require


def main(argv=None,*,release,execute=None):
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument("--approved-runtime-manifest")
    parser.add_argument("--runtime-mode",choices=("shared","release"),default="shared")
    parser.add_argument("--preflight",action="store_true")
    args,forwarded=parser.parse_known_args(argv)
    try:
        _require(sys.flags.isolated,"ISOLATION_REQUIRED")
        python,evidence=authenticate_control(release,args.runtime_mode,expected_manifest=args.approved_runtime_manifest)
        _require(os.environ.get("HOPE_DEPLOYED_COMMIT")==Path(release).name
            and os.environ.get("HOPE_LIVE_ENABLED")=="false","PAPER_DEPLOYMENT_UNVERIFIED")
        if args.approved_runtime_manifest is None:
            _require(sys.flags.no_site,"NO_SITE_REQUIRED")
            environment={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LANG":"C","LC_ALL":"C"}
            for name in ("HOPE_DATABASE_URL","HOPE_DEPLOYED_COMMIT","HOPE_LIVE_ENABLED","NOTIFY_SOCKET","WATCHDOG_PID","WATCHDOG_USEC"):
                if name in os.environ:environment[name]=os.environ[name]
            arguments=[str(python),"-I","-B","-X",CACHE_PREFIX,str(Path(release)/"deploy/operations/paper_release.py"),
                "--approved-runtime-manifest",evidence["manifest_sha256"],"--runtime-mode",args.runtime_mode]
            if args.preflight:arguments.append("--preflight")
            return (execute or os.execve)(str(python),arguments+forwarded,environment)
        verify_interpreter(python)
        _require(sys.version_info[:2]==(3,13),"PYTHON_VERSION_REQUIRED")
        if args.preflight:
            _require(not forwarded,"PREFLIGHT_ARGUMENTS_INVALID")
            from hope.api.operations_deployment import main as preflight
            return preflight(["--release",str(release),"--expected-sha",Path(release).name,"--database-component","paper"])
        from hope.api.paper_service import main as worker
        return worker(forwarded)
    except Exception:
        parser.exit(2,"PAPER_BOOTSTRAP_BLOCKED REVIEW_SOURCE_RUNTIME_AND_PROVENANCE\n")
