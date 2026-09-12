import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_postgres_migrations_apply_and_are_idempotent() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")
        first = apply_migrations(connection, migrations_dir)
        second = apply_migrations(connection, migrations_dir)
        assert first == [
            "001_initial.sql",
            "002_integrity_constraints.sql",
            "003_experiment_invalidation_events.sql",
            "004_identity_universe_integrity.sql",
            "005_trading_integrity_guards.sql",
            "006_cross_entity_trading_integrity.sql",
            "007_identity_namespace_integrity.sql",
            "008_migration_checksums.sql",
            "009_job_run_identity.sql",
            "010_job_run_lifecycle.sql",
            "011_paper_effect_idempotency.sql",
            "012_paper_portfolio_state.sql",
            "013_paper_portfolio_pnl_events.sql",
            "014_market_bars.sql",
            "015_market_data_immutability.sql",
            "016_dataset_version_identity_immutability.sql",
            "017_dataset_metadata_immutability.sql",
            "018_experiment_dataset_freeze.sql",
            "019_configuration_snapshot_immutability.sql",
            "020_strategy_version_immutability.sql",
            "021_experiment_universe_freeze.sql",
            "022_experiment_provenance_immutability.sql",
            "023_strategy_metadata_immutability.sql",
            "024_experiment_universe_cardinality.sql",
            "025_experiment_invalidation_immutability.sql",
            "026_universe_metadata_immutability.sql",
            "027_audit_event_immutability.sql",
            "028_signal_immutability.sql",
            "029_fill_immutability.sql",
            "030_order_immutability.sql",
            "031_paper_effect_immutability.sql",
            "032_paper_portfolio_fill_application_immutability.sql",
            "033_paper_portfolio_pnl_event_immutability.sql",
            "034_job_run_terminal_immutability.sql",
            "035_job_run_identity_immutability.sql",
            "036_paper_portfolio_pnl_application_integrity.sql",
            "037_paper_portfolio_pnl_instrument_integrity.sql",
            "038_paper_portfolio_pnl_time_integrity.sql",
            "039_paper_portfolio_pnl_commission_integrity.sql",
        ]
        assert second == []
        assert connection.execute(text("SELECT 1 FROM information_schema.tables WHERE table_name='experiments'")).scalar_one() == 1
        assert connection.execute(text("SELECT 1 FROM information_schema.tables WHERE table_name='job_runs'")).scalar_one() == 1
        assert connection.execute(text("SELECT 1 FROM information_schema.tables WHERE table_name='paper_effects'")).scalar_one() == 1
        assert connection.execute(text("SELECT 1 FROM information_schema.tables WHERE table_name='paper_portfolio_pnl_events'")).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='job_runs' AND column_name='status'"
            )
        ).scalar_one() == 1
