"""Read-only observation is usable under HALT and never changes durable runs."""
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from test_certified_paper_bridge import certified_case
from hope.application.jobs import JobRunStatus, create_job_run_completion, create_scheduled_job_run
from hope.infrastructure.paper_runtime import run_paper_once
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository
from hope.infrastructure.scheduling.paper_recovery_inspection import inspect_paper_runs


def _observe(engine, ids):
    with engine.connect().execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True) as connection:
        with connection.begin():
            return inspect_paper_runs(connection, ids)


@pytest.mark.integration
@pytest.mark.parametrize("halted", [False, True])
@pytest.mark.parametrize("path", ["NO_TRADE", "FILLED", "RISK_BLOCKED"])
def test_committed_observation_preserves_control_history_and_effects(certified_case, halted, path):
    engine, make = certified_case
    data, run, strategy, work, registry = make(emit=path != "NO_TRADE", blocked=path == "RISK_BLOCKED")
    run_paper_once(engine, run, registry, now=lambda: data["as_of"] + timedelta(minutes=1))
    if halted:
        with engine.begin() as connection:
            SqlAlchemyPaperEnvironmentControlRepository(connection).transition("RUNNING", "HALTED", "Inspection test", actor="integration-test")
    def snapshot():
        with engine.connect() as connection:
            return tuple(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in ("job_runs", "paper_effects", "audit_events", "paper_environment_control_events",
                              "paper_strategy_authorizations", "paper_certified_outcomes", "paper_portfolios"))
    try:
        before = snapshot()
        statements = []
        def observe_statement(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.strip().upper())
        event.listen(engine, "before_cursor_execute", observe_statement)
        try:
            result = _observe(engine, (run.job_run_id,))
        finally:
            event.remove(engine, "before_cursor_execute", observe_statement)
        assert statements and all(s.startswith("SELECT") for s in statements)
        assert result["control_state"] == ("HALTED" if halted else "RUNNING")
        assert result["recovery_authorized"] is False and result["live_enabled"] is False
        observed = result["runs"][0]
        assert observed["lifecycle"] == "SUCCEEDED" and observed["topology"] == "COMPLETE"
        assert observed["proof"] == "NOT_VERIFIED"
        assert observed["authorization_present"] and observed["outcome_present"]
        assert not observed["reconciliation_present"]
        assert sum(observed["effect_counts"].values()) == (5 if path == "FILLED" else 1)
        assert snapshot() == before
        assert len(strategy.calls) == 1
    finally:
        if halted:
            with engine.begin() as connection:
                SqlAlchemyPaperEnvironmentControlRepository(connection).transition("HALTED", "RUNNING", "Inspection cleanup", actor="integration-test")


@pytest.mark.integration
def test_snapshot_remains_consistent_across_concurrent_terminalization_and_halt(certified_case):
    engine, make = certified_case
    data, _, _, _, _ = make()
    run = create_scheduled_job_run(f"paper:USA:{data['strategy_version_id']}:inspect-snapshot", data["as_of"])
    with engine.begin() as connection:
        assert SqlAlchemyJobRunRepository(connection).claim(run)
    changed = []
    def commit_peer(conn, cursor, statement, parameters, context, executemany):
        if "GROUP BY job_run_id,effect_type" in statement and not changed:
            changed.append(True)
            with engine.begin() as peer:
                assert SqlAlchemyJobRunRepository(peer).complete(create_job_run_completion(run,
                    JobRunStatus.FAILED, data["as_of"]+timedelta(minutes=1), failure_code="EXPECTED_INSPECTION_TEST"))
                SqlAlchemyPaperEnvironmentControlRepository(peer).transition("RUNNING", "HALTED", "Snapshot test", actor="integration-test")
    event.listen(engine, "before_cursor_execute", commit_peer)
    try:
        first = _observe(engine, (run.job_run_id,))
        assert changed == [True]
        assert first["control_state"] == "RUNNING"
        assert first["runs"][0]["lifecycle"] == "CLAIMED"
        second = _observe(engine, (run.job_run_id,))
        assert second["control_state"] == "HALTED"
        assert second["runs"][0]["lifecycle"] == "FAILED"
        assert second["runs"][0]["effect_counts"] == {}
    finally:
        event.remove(engine, "before_cursor_execute", commit_peer)
        with engine.begin() as connection:
            if SqlAlchemyPaperEnvironmentControlRepository(connection).current_state() == "HALTED":
                SqlAlchemyPaperEnvironmentControlRepository(connection).transition("HALTED", "RUNNING", "Snapshot cleanup", actor="integration-test")


@pytest.mark.integration
@pytest.mark.parametrize("readonly,isolation", [(False,"READ COMMITTED"),(True,"READ COMMITTED"),(False,"REPEATABLE READ")])
def test_inspection_refuses_inappropriate_transaction_mode(certified_case, readonly, isolation):
    engine, _ = certified_case
    with engine.connect().execution_options(isolation_level=isolation, postgresql_readonly=readonly) as connection:
        with pytest.raises(RuntimeError, match="REQUIRES_READ_ONLY_REPEATABLE_READ"):
            inspect_paper_runs(connection, (uuid4(),))


@pytest.mark.integration
def test_cli_emits_observations_only_and_marks_missing_identity(certified_case, monkeypatch, capsys):
    from hope.api.paper_recovery_inspect import main
    engine, make = certified_case
    data, run, _, _, registry = make()
    run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1))
    missing = uuid4()
    assert main(["--run-id",str(run.job_run_id),"--run-id",str(missing),
        "--migrations-dir",str(Path(__file__).parents[2]/"migrations")]) == 0
    import json
    result = json.loads(capsys.readouterr().out)
    assert result["recovery_authorized"] is False
    assert result["runs"][1]["lifecycle"] == "MISSING"
    assert "job_key" not in str(result) and "inputs_hash" not in str(result)


@pytest.mark.integration
def test_maximum_selection_preserves_order_and_does_not_discover_other_runs(certified_case):
    engine, make = certified_case
    data, run, _, _, registry = make()
    run_paper_once(engine, run, registry, now=lambda: data["as_of"]+timedelta(minutes=1))
    selected = tuple(uuid4() for _ in range(16)) + (run.job_run_id,) + tuple(uuid4() for _ in range(15))
    result = _observe(engine, selected)
    assert tuple(item["run_id"] for item in result["runs"]) == tuple(map(str,selected))
    assert sum(item["lifecycle"] == "MISSING" for item in result["runs"]) == 31
    assert result["runs"][16]["lifecycle"] == "SUCCEEDED"


@pytest.mark.integration
def test_nonpaper_identity_is_out_of_scope_and_database_rejects_writes(certified_case):
    from sqlalchemy.exc import DBAPIError
    engine, _ = certified_case
    from datetime import UTC,datetime
    run = create_scheduled_job_run(f"research:inspection:{uuid4()}",datetime(2026,1,1,tzinfo=UTC))
    with engine.begin() as connection:
        assert SqlAlchemyJobRunRepository(connection).claim(run)
        assert SqlAlchemyJobRunRepository(connection).complete(create_job_run_completion(run,
            JobRunStatus.FAILED,run.scheduled_for+timedelta(minutes=1),failure_code="EXPECTED_INSPECTION_TEST"))
    with engine.connect().execution_options(isolation_level="REPEATABLE READ",postgresql_readonly=True) as connection:
        result = inspect_paper_runs(connection,(run.job_run_id,))
        assert result["runs"][0]["lifecycle"] == "OUT_OF_SCOPE"
        assert result["runs"][0]["effect_counts"] == {}
        with pytest.raises(DBAPIError) as exc:
            connection.execute(text("DELETE FROM job_runs WHERE FALSE"))
        assert exc.value.orig.sqlstate == "25006"
