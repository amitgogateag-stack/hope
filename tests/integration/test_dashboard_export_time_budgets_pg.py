"""Real PostgreSQL contention/slow reads cannot publish partial dashboard files."""
import json
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from hope.api import dashboard_snapshot as module


@pytest.fixture
def dashboard_schema(postgres_engine):
    schema = "dashboard_budget_" + uuid4().hex
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        for statement in (
            "CREATE TABLE research_runs (id integer)",
            "CREATE TABLE paper_environment_control_events (control_sequence integer, state text)",
            "CREATE TABLE job_runs (job_run_id uuid, job_key text, status text, created_at timestamptz)",
            "CREATE TABLE paper_effects (job_run_id uuid)",
            "CREATE TABLE audit_events (entity_id text, event_type text, entity_type text)",
            "INSERT INTO paper_environment_control_events VALUES (1, 'HALTED')",
        ):
            connection.execute(text(statement))
    try:
        yield schema
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["lock", "statement"])
def test_export_timeout_preserves_previous_snapshot_and_recovers(
    postgres_engine, dashboard_schema, tmp_path, monkeypatch, failure,
):
    target = tmp_path / "snapshot.json"
    target.write_bytes(b"last complete snapshot")
    url = postgres_engine.url.update_query_dict({"options": f"-csearch_path={dashboard_schema}"})
    original_count = module._count
    with postgres_engine.connect() as blocker:
        if failure == "lock":
            blocker.execute(text(f'LOCK TABLE "{dashboard_schema}".research_runs IN ACCESS EXCLUSIVE MODE'))
        else:
            def slow_count(connection, query):
                connection.execute(text("SELECT pg_sleep(2)"))
                return original_count(connection, query)
            monkeypatch.setattr(module, "_count", slow_count)
        started = monotonic()
        try:
            with pytest.raises(DBAPIError) as error:
                module.export_dashboard_snapshot(
                    target, url, connect_timeout_seconds=2,
                    statement_timeout_ms=500, lock_timeout_ms=100,
                )
            assert error.value.orig.sqlstate == ("55P03" if failure == "lock" else "57014")
            assert monotonic() - started < 5
            assert target.read_bytes() == b"last complete snapshot"
            assert list(tmp_path.iterdir()) == [target]
        finally:
            blocker.rollback()
            monkeypatch.setattr(module, "_count", original_count)
    # A fresh export after the competing lock/slow read is removed succeeds;
    # the failure neither poisoned a transaction nor changed durable HOPE data.
    module.export_dashboard_snapshot(target, url)
    snapshot = json.loads(target.read_bytes())
    assert snapshot["paper"]["environment_state"] == "HALTED"
    assert snapshot["paper"]["job_runs"]["total"] == 0
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.integration
def test_read_timeouts_do_not_escape_the_snapshot_transaction(postgres_engine, dashboard_schema):
    with postgres_engine.connect() as connection:
        connection.execute(text(f'SET search_path TO "{dashboard_schema}"'))
        connection.commit()
        before = tuple(connection.execute(text(f"SHOW {setting}")).scalar_one()
                       for setting in ("statement_timeout", "lock_timeout"))
        connection.rollback()
        module.build_dashboard_snapshot(connection, statement_timeout_ms=900, lock_timeout_ms=300)
        assert connection.execute(text("SHOW statement_timeout")).scalar_one() == "900ms"
        assert connection.execute(text("SHOW lock_timeout")).scalar_one() == "300ms"
        assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        connection.rollback()
        after = tuple(connection.execute(text(f"SHOW {setting}")).scalar_one()
                      for setting in ("statement_timeout", "lock_timeout"))
        assert after == before
        connection.rollback()
