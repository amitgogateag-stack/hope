"""Explicit certified PAPER recovery CLI; no execution factory or deployment."""
import argparse
from datetime import UTC, datetime
import json
import os
from uuid import UUID

from sqlalchemy import create_engine

from hope.infrastructure.paper_service import verify_schema_compatibility
from hope.infrastructure.scheduling.certified_paper_recovery import recover_certified_paper_batch


def _canonical_uuid(value):
    try:
        parsed = UUID(value)
        if str(parsed) == value:
            return parsed
    except (TypeError, ValueError):
        pass
    raise argparse.ArgumentTypeError("canonical UUID required")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Authenticate explicit historical PAPER runs; never executes trades")
    parser.add_argument("--run-id", required=True, action="append", type=_canonical_uuid)
    parser.add_argument("--migrations-dir", required=True)
    args = parser.parse_args(argv)
    if not 1 <= len(args.run_id) <= 32 or len(set(args.run_id)) != len(args.run_id):
        parser.exit(2, "PAPER_CERTIFIED_RECOVERY_SELECTION_INVALID\n")
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        parser.exit(2, "PAPER_CERTIFIED_RECOVERY_DATABASE_CONFIGURATION_REQUIRED\n")
    engine = None
    try:
        engine = create_engine(url, pool_pre_ping=True, pool_timeout=10,
            connect_args={"connect_timeout": 10, "options": "-c statement_timeout=30000 -c lock_timeout=10000"})
        with engine.begin() as connection:
            verify_schema_compatibility(connection, args.migrations_dir)
            result = recover_certified_paper_batch(connection, args.run_id, current=datetime.now(UTC))
        # Publish only after a successful outer commit, including its ACK.
        print(json.dumps(dict(status="VERIFIED", paper_only=True, live_enabled=False,
            reconciled_run_ids=[str(v) for v in result.reconciled_run_ids],
            authenticated_run_ids=[str(v) for v in result.authenticated_run_ids]), sort_keys=True))
        return 0
    except Exception:
        parser.exit(1, "PAPER_CERTIFIED_RECOVERY_UNAVAILABLE\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
