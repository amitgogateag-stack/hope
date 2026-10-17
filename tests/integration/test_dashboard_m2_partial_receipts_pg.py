"""Unsupported/partial authorization history is unknown, never certified zero."""
from uuid import uuid4

import pytest
from sqlalchemy import text

from hope.api.dashboard_snapshot import _optional_projections
from hope.api.dashboard_m2_snapshot import readers
from hope.api.dashboard_projection import RISK_BLOCK_REASONS


@pytest.mark.integration
def test_partial_or_corrupt_authorization_receipts_are_unavailable(postgres_engine):
    schema = "dashboard_partial_" + uuid4().hex
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        for query in (
            "CREATE TABLE job_runs(job_run_id uuid,job_key text,status text,failure_code text,created_at timestamptz)",
            "CREATE TABLE strategy_candidate_classifications(classification_id uuid,strategy_version_id uuid,research_decision_id text,state text,markets text[])",
            "CREATE VIEW current_strategy_candidate_classifications AS SELECT * FROM strategy_candidate_classifications",
            "CREATE TABLE paper_strategy_authorizations(job_run_id uuid,strategy_version_id uuid,market text,classification_id uuid,research_decision_id text,created_at timestamptz,database_principal text)",
            "CREATE TABLE research_runs(research_run_id uuid,experiment_id text)",
            "CREATE TABLE research_decisions(decision_id text,variant_run_id uuid,decision text)",
            "CREATE TABLE experiments(experiment_id text,strategy_version_id uuid,dataset_version_id uuid,universe_version_id uuid,configuration_hash char(64))",
            "CREATE TABLE datasets(dataset_id uuid,pit_certified boolean,source text)",
            "CREATE TABLE dataset_versions(dataset_version_id uuid,dataset_id uuid,immutable boolean)",
            "CREATE TABLE paper_certified_outcomes(job_run_id uuid,source_run_id uuid,dataset_version_id uuid,universe_version_id uuid,configuration_hash char(64),inputs_hash char(64),decision_time timestamptz,outcome text,reason_code text,created_at timestamptz)",
        ):
            connection.execute(text(query))
    def project():
        with postgres_engine.connect() as connection:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.commit()
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY"))
            generated = connection.execute(text("SELECT CURRENT_TIMESTAMP")).scalar_one()
            value = _optional_projections(connection, generated)
            connection.rollback()
            connection.execute(text("RESET search_path"))
            connection.commit()
            return value
    try:
        empty = project()
        assert empty["certified_chain"] == {"status": "AVAILABLE", "authorization_receipts": 0, "operational_classifications": 0, "latest_authorization_at": None, "eligibility_policy": "UNKNOWN"}
        assert empty["certified_outcomes"] == {"status": "AVAILABLE", "total": 0, "claimed_records": 0, "latest_decision_at": None, "by_outcome": {"NO_TRADE": 0, "RISK_BLOCKED": 0, "FILLED": 0}, "risk_blocked_reasons": {key: 0 for key in RISK_BLOCK_REASONS}}
        identities = {key: uuid4() for key in ("job", "version", "classification", "source", "variant", "dataset", "dataset_id", "universe")}
        identities["hash"] = "a" * 64
        identities["job_key"] = f"paper:USA:{identities['version']}:fixture"
        with postgres_engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            # Intentionally untrusted legacy-like schema: no guards/constraints.
            # Every projected structural link matches except the source run is
            # not the authorization decision's variant run. This must not be
            # labeled a certified historical outcome even for the same version.
            for query in (
                "INSERT INTO job_runs VALUES (:job,:job_key,'CLAIMED',NULL,CURRENT_TIMESTAMP)",
                "INSERT INTO strategy_candidate_classifications VALUES (:classification,:version,'private','OPERATIONAL_CANDIDATE',ARRAY['USA'])",
                "INSERT INTO paper_strategy_authorizations VALUES (:job,:version,'USA',:classification,'private',CURRENT_TIMESTAMP,'private_principal')",
                "INSERT INTO research_runs VALUES (:source,'private-experiment')",
                "INSERT INTO research_decisions VALUES ('private',:variant,'SUPPORTED')",
                "INSERT INTO experiments VALUES ('private-experiment',:version,:dataset,:universe,:hash)",
                "INSERT INTO datasets VALUES (:dataset_id,TRUE,'TEST')",
                "INSERT INTO dataset_versions VALUES (:dataset,:dataset_id,TRUE)",
                "INSERT INTO paper_certified_outcomes VALUES (:job,:source,:dataset,:universe,:hash,:hash,CURRENT_TIMESTAMP,'NO_TRADE','NO_SIGNAL',CURRENT_TIMESTAMP)",
            ):
                connection.execute(text(query), identities)
        mismatched = project()
        assert mismatched["certified_chain"]["authorization_receipts"] == 1
        assert mismatched["certified_outcomes"] == {"status": "UNAVAILABLE"}
        with postgres_engine.begin() as connection:
            connection.execute(text(f'UPDATE "{schema}".strategy_candidate_classifications SET state=\'RESEARCH\''))
        corrupt = project()
        assert corrupt["certified_chain"] == {"status": "UNAVAILABLE"}
        assert corrupt["certified_outcomes"] == {"status": "UNAVAILABLE"}
        assert corrupt["blocked_runs"]["status"] == "AVAILABLE"
        with postgres_engine.begin() as connection:
            connection.execute(text(f'ALTER TABLE "{schema}".paper_strategy_authorizations DROP COLUMN market'))
        unsupported = project()
        assert unsupported["certified_chain"] == {"status": "UNAVAILABLE"}
        assert "private_principal" not in str(unsupported)
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.mark.integration
def test_execution_observations_report_missing_applications_and_accounting_without_valuation(postgres_engine):
    schema, order, fill, portfolio = "dashboard_ledger_" + uuid4().hex, uuid4(), uuid4(), uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        for query in (
            "CREATE TABLE orders(order_id uuid,environment text)",
            "CREATE TABLE fills(fill_id uuid,order_id uuid)",
            "CREATE TABLE paper_portfolio_fill_applications(portfolio_id uuid,fill_id uuid)",
            "CREATE TABLE paper_portfolio_pnl_events(portfolio_id uuid,fill_id uuid)",
        ):
            connection.execute(text(query))
        connection.execute(text("INSERT INTO orders VALUES (:id,'PAPER')"), {"id": order})
        connection.execute(text("INSERT INTO fills VALUES (:id,:order)"), {"id": fill, "order": order})
    def observe():
        with postgres_engine.connect() as connection:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.commit()
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY"))
            generated = connection.execute(text("SELECT CURRENT_TIMESTAMP")).scalar_one()
            value = readers(connection, generated)["execution_ledger"]()
            connection.rollback()
            connection.execute(text("RESET search_path"))
            connection.commit()
            return value
    try:
        initial = observe()
        assert initial == {"fills": 1, "applied_fills": 0, "fill_applications": 0, "accounting_receipts": 0, "fills_without_application": 1, "applications_without_pnl": 0}
        with postgres_engine.begin() as connection:
            connection.execute(text(f'INSERT INTO "{schema}".paper_portfolio_fill_applications VALUES (:portfolio,:fill)'), {"portfolio": portfolio, "fill": fill})
        partial = observe()
        assert partial["fills_without_application"] == 0 and partial["applications_without_pnl"] == 1
        with postgres_engine.begin() as connection:
            connection.execute(text(f'INSERT INTO "{schema}".paper_portfolio_pnl_events VALUES (:portfolio,:fill)'), {"portfolio": portfolio, "fill": fill})
        complete = observe()
        assert complete["applications_without_pnl"] == 0 and complete["accounting_receipts"] == 1
        assert "portfolio_id" not in complete and "pnl_amount" not in complete
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
