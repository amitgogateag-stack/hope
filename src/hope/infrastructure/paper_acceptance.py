"""Controlled single-run acceptance; existing certified runtime owns all effects."""
from datetime import UTC,datetime
import json
import os
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from hope.api.dashboard_server import _verified_operations
from hope.api.operations_deployment import verify_database_role
from hope.infrastructure.paper_service import PaperServicePlan, publish_service_status, run_service, verify_schema_compatibility
from hope.infrastructure.scheduling.paper import build_operational_paper_runs
from hope.infrastructure.scheduling.certified_paper_recovery import recover_certified_paper_batch


_ACCEPTANCE_LOCK_NAMESPACE = "hope:m3:controlled-paper-acceptance"


def _controlled_acceptance_lock(engine, run_id: UUID):
    """Hold one transaction-scoped lock across the complete acceptance proof."""
    connection = engine.connect()
    try:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        acquired = connection.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:lock_name)::bigint)"),
            {"lock_name": f"{_ACCEPTANCE_LOCK_NAMESPACE}:{run_id}"},
        ).scalar_one()
        if acquired is not True:
            raise RuntimeError("M3_ACCEPTANCE_CONCURRENT_RUN")
        return connection
    except BaseException:
        connection.close()
        raise


def controlled_paper_acceptance(engine, factory, *, expected_database: str,
        expected_sha: str, run_id: UUID, migrations_dir: Path, evidence_dir: Path,
        attestation: dict, now=lambda:datetime.now(UTC)) -> dict:
    """Caller must separately authorize this operational action on an isolated DB.

    A failed post-commit check cannot roll back already committed PAPER history.
    Preserve the evidence directory and investigate; never auto-retry a fresh ID.
    """
    if (type(run_id) is not UUID or not expected_database or attestation.get("sha")!=expected_sha
            or not isinstance(expected_sha,str) or re.fullmatch(r"[0-9a-f]{40}",expected_sha) is None
            or os.getenv("HOPE_DEPLOYED_COMMIT")!=expected_sha):
        raise ValueError("M3_ACCEPTANCE_IDENTITY_INVALID")
    lock = _controlled_acceptance_lock(engine,run_id)
    try:
        return _controlled_paper_acceptance_locked(engine,factory,expected_database=expected_database,
            expected_sha=expected_sha,run_id=run_id,migrations_dir=migrations_dir,
            evidence_dir=evidence_dir,attestation=attestation,now=now)
    finally:
        lock.close()


def _controlled_paper_acceptance_locked(engine, factory, *, expected_database: str,
        expected_sha: str, run_id: UUID, migrations_dir: Path, evidence_dir: Path,
        attestation: dict, now) -> dict:
    def target(connection):
        if connection.execute(text("SELECT current_database()")).scalar_one()!=expected_database:
            raise RuntimeError("M3_DATABASE_MISMATCH")
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        target(connection)
        verify_database_role(connection,component="paper")
        verify_schema_compatibility(connection,migrations_dir)
        postgres_version=int(connection.execute(text("SHOW server_version_num")).scalar_one())
        migration_count=connection.execute(text("SELECT count(*) FROM hope_schema_migrations")).scalar_one()
        if connection.execute(text("SELECT EXISTS(SELECT 1 FROM job_runs WHERE job_run_id=:id)"),{"id":run_id}).scalar_one():
            raise RuntimeError("M3_FRESH_ACCEPTANCE_RUN_REQUIRED")
        if connection.execute(text("SELECT EXISTS(SELECT 1 FROM orders WHERE environment='LIVE')")).scalar_one():
            raise RuntimeError("M3_ISOLATED_PAPER_DATABASE_REQUIRED")

    def bounded_factory(connection,current):
        target(connection)
        plan = factory(connection,current)
        if not isinstance(plan,PaperServicePlan):
            raise RuntimeError("M3_REVIEWED_PLAN_REQUIRED")
        selected = build_operational_paper_runs(plan.bindings,plan.schedules,plan.calendars,
            start=plan.start,end=plan.end)
        if len(selected)!=1 or selected[0].job_run_id!=run_id:
            raise RuntimeError("M3_EXACT_SINGLE_RUN_REQUIRED")
        return plan

    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(mode=0o700,parents=False,exist_ok=False)
    status = evidence_dir/"health.json"
    for executed,skipped in ((1,0),(0,1)):
        code = run_service(engine,bounded_factory,migrations_dir=migrations_dir,status_file=status,
            once=True,require_certified=True,now=now)
        health = json.loads(_verified_operations(status))
        if (code!=0 or health["state"]!="STOPPED" or health["deployed_commit"]!=expected_sha
                or health["schema_compatibility"]!="COMPATIBLE" or health["cycles_succeeded"]!=1
                or health["cycles_failed"]!=0 or health["jobs_executed"]!=executed
                or health["jobs_skipped"]!=skipped):
            raise RuntimeError("M3_CYCLE_OR_RESTART_NOT_VERIFIED")
    with engine.begin() as connection:
        target(connection)
        recovery = recover_certified_paper_batch(connection,(run_id,),current=now())
        if recovery.reconciled_run_ids or recovery.authenticated_run_ids!=(run_id,):
            raise RuntimeError("M3_RECONCILIATION_NOT_VERIFIED")
        outcome = connection.execute(text("SELECT outcome FROM paper_certified_outcomes WHERE job_run_id=:id"),{"id":run_id}).scalar_one()
        if outcome not in ("FILLED","NO_TRADE","RISK_BLOCKED"):
            raise RuntimeError("M3_OUTCOME_NOT_VERIFIED")
    result = dict(schema="hope.m3-acceptance.v1",status="BOUNDED_PAPER_VERIFIED",
        paper_only=True,live_enabled=False,database=expected_database,run_id=str(run_id),outcome=outcome,
        release=attestation,initial_executed=1,restart_skipped=1,reconciliation="AUTHENTICATED",
        worker_state="STOPPED",dashboard_review="PENDING",operator_review_required=True)
    result.update(postgres_version_num=postgres_version,migrations=migration_count)
    publish_service_status(evidence_dir/"acceptance.json",result)
    return result
