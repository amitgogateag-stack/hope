"""Explicitly authorized isolated-database one-run acceptance, never a daemon."""
import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine

from hope.api.operations_deployment import inspect_release
from hope.api.paper_service import load_plan_factory
from hope.api.release_attestation import verify_exact_ci
from hope.infrastructure.paper_acceptance import controlled_paper_acceptance


def main(argv=None):
    parser = argparse.ArgumentParser(description="Authorize exactly one isolated PAPER acceptance run and its no-replay restart")
    parser.add_argument("--approve-one-paper-cycle",action="store_true",required=True)
    parser.add_argument("--expected-database",choices=("hope_m3_restore_test",),required=True)
    parser.add_argument("--release",type=Path,required=True)
    parser.add_argument("--expected-sha",required=True)
    parser.add_argument("--ci-run-id",type=int,required=True)
    parser.add_argument("--ci-run-attempt",type=int,required=True)
    parser.add_argument("--factory",required=True)
    parser.add_argument("--run-id",type=UUID,required=True)
    parser.add_argument("--evidence-dir",type=Path,required=True)
    args = parser.parse_args(argv)
    engine = None
    try:
        if (os.getenv("HOPE_EXECUTION_MODE")!="PAPER" or os.getenv("HOPE_LIVE_ENABLED")!="false"
                or os.getenv("HOPE_DEPLOYED_COMMIT")!=args.expected_sha or not os.getenv("HOPE_DATABASE_URL")):
            raise RuntimeError("M3_EXPLICIT_PAPER_CONFIGURATION_REQUIRED")
        inspect_release(args.release,args.expected_sha)
        if (not args.evidence_dir.is_absolute() or args.evidence_dir.exists()
                or args.evidence_dir.resolve().is_relative_to(args.release.resolve())):
            raise RuntimeError("M3_PRIVATE_EXTERNAL_EVIDENCE_DIRECTORY_REQUIRED")
        attestation = verify_exact_ci(args.expected_sha,args.ci_run_id,args.ci_run_attempt)
        factory = load_plan_factory(args.factory)
        engine = create_engine(os.environ["HOPE_DATABASE_URL"],pool_pre_ping=True,pool_timeout=10,
            connect_args={"connect_timeout":10,"options":"-c statement_timeout=30000 -c lock_timeout=10000"})
        result = controlled_paper_acceptance(engine,factory,expected_database=args.expected_database,
            expected_sha=args.expected_sha,run_id=args.run_id,migrations_dir=args.release/"migrations",
            evidence_dir=args.evidence_dir,attestation=attestation)
        print(json.dumps(result,sort_keys=True))
        return 0
    except Exception:
        parser.exit(2,"M3_ACCEPTANCE_BLOCKED_REVIEW_DURABLE_STATE\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__=="__main__":
    raise SystemExit(main())
