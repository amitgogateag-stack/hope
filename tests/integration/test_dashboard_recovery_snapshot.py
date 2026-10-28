"""Private dashboard projection stays read-only across concurrent commits."""
from uuid import uuid4
import json
from datetime import datetime,timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hope.api import dashboard_snapshot as module
from hope.api.snapshot_contract import snapshot_evidence


@pytest.mark.integration
@pytest.mark.parametrize("environment",["RUNNING","HALTED"])
def test_recovery_projection_retains_one_snapshot_during_concurrent_commit(postgres_engine, monkeypatch,environment):
    # Dedicated disposable schema: no reads or writes to stored HOPE application
    # data. Minimal tables exercise the actual PostgreSQL aggregate queries.
    schema = "dashboard_test_" + uuid4().hex
    with postgres_engine.begin() as setup:
        setup.execute(text(f'CREATE SCHEMA "{schema}"'))
        setup.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        setup.execute(text("CREATE TABLE research_runs (id integer)"))
        setup.execute(text("CREATE TABLE paper_environment_control_events "
                           "(control_sequence integer, state text)"))
        setup.execute(text("CREATE TABLE job_runs "
                           "(job_run_id uuid, job_key text, status text, created_at timestamptz)"))
        setup.execute(text("CREATE TABLE paper_effects (job_run_id uuid)"))
        setup.execute(text("CREATE TABLE audit_events (entity_id text, event_type text, entity_type text)"))
        setup.execute(text("INSERT INTO paper_environment_control_events VALUES (1, :state)"),{"state":environment})
        setup.execute(text("INSERT INTO job_runs VALUES (:id, 'paper:private', 'CLAIMED', "
                           "CURRENT_TIMESTAMP - INTERVAL '2 hours')"), {"id": uuid4()})
    original_count = module._count
    injected = False
    try:
        with postgres_engine.connect() as reader, postgres_engine.connect() as writer:
            for connection in (reader, writer):
                connection.execute(text(f'SET search_path TO "{schema}"'))
                connection.commit()

            def count_after_writer_commits(connection, query):
                nonlocal injected
                if not injected:
                    injected = True
                    # Database time has established the reader's snapshot.
                    # A competing worker both finishes the claim and adds data.
                    writer.execute(text("UPDATE job_runs SET status = 'SUCCEEDED'"))
                    writer.execute(text("INSERT INTO research_runs VALUES (1)"))
                    writer.commit()
                return original_count(connection, query)

            monkeypatch.setattr(module, "_count", count_after_writer_commits)
            snapshot = module.build_dashboard_snapshot(reader,deployed_commit="a"*40)
            assert injected
            assert snapshot["research"]["run_count"] == 0
            assert snapshot["paper"]["job_runs"]["claimed"] == 1
            assert snapshot["paper"]["recovery"] == {
                "claimed_with_effects": 0, "claimed_without_effects": 1,
                "claims_older_than_one_hour": 1, "reconciliation_receipts": 0,
            }
            evidence,blockers=snapshot_evidence(json.dumps(snapshot).encode(),expected_commit="a"*40,
                observed_at=datetime.now(timezone.utc),status_timestamp=snapshot["generated_at"])
            assert "PAPER_RECOVERY_REQUIRED" in blockers
            assert evidence["reconciliation"]=="RECOVERY_REQUIRED"
            assert ("PAPER_DURABLE_HALT_OBSERVED" in blockers)==(environment=="HALTED")
            with pytest.raises(DBAPIError) as error:
                reader.execute(text("INSERT INTO research_runs VALUES (2)"))
            assert error.value.orig.sqlstate == "25006"  # read_only_sql_transaction
            reader.rollback()
            # The next export sees the complete new generation, never a mix.
            refreshed = module.build_dashboard_snapshot(reader,deployed_commit="a"*40)
            assert refreshed["research"]["run_count"] == 1
            assert refreshed["paper"]["job_runs"]["succeeded"] == 1
            assert refreshed["paper"]["recovery"]["claimed_without_effects"] == 0
            evidence,blockers=snapshot_evidence(json.dumps(refreshed).encode(),expected_commit="a"*40,
                observed_at=datetime.now(timezone.utc),status_timestamp=refreshed["generated_at"])
            assert "PAPER_RECOVERY_REQUIRED" not in blockers
            assert evidence["job_runs"]["succeeded"]==1
            reader.rollback()
    finally:
        with postgres_engine.begin() as cleanup:
            cleanup.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
