"""Explicit read-only PAPER diagnostics, including during HALT."""
import argparse
import json
import os

from sqlalchemy import create_engine

from hope.api.paper_recovery import _canonical_uuid
from hope.infrastructure.paper_service import verify_schema_compatibility
from hope.infrastructure.scheduling.paper_recovery_inspection import MAX_INSPECTED_RUNS, inspect_paper_runs


def main(argv=None):
    parser = argparse.ArgumentParser(description="Observe explicit PAPER runs without authorizing recovery")
    parser.add_argument("--run-id", required=True, action="append", type=_canonical_uuid)
    parser.add_argument("--migrations-dir", required=True)
    args = parser.parse_args(argv)
    if not 1 <= len(args.run_id) <= MAX_INSPECTED_RUNS or len(set(args.run_id)) != len(args.run_id):
        parser.exit(2, "PAPER_INSPECTION_SELECTION_INVALID\n")
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        parser.exit(2, "PAPER_INSPECTION_DATABASE_CONFIGURATION_REQUIRED\n")
    engine = None
    try:
        engine = create_engine(url, pool_pre_ping=True, pool_timeout=10,
            connect_args={"connect_timeout": 10, "options": "-c statement_timeout=15000 -c lock_timeout=1000"})
        with engine.connect().execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True) as connection:
            with connection.begin():
                verify_schema_compatibility(connection, args.migrations_dir)
                result = inspect_paper_runs(connection, args.run_id)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception:
        parser.exit(1, "PAPER_INSPECTION_UNAVAILABLE\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
