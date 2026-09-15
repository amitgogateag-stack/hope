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
            "040_fill_cost_nonnegative.sql",
            "041_fill_cumulative_quantity_integrity.sql",
            "042_fill_cost_model_provenance.sql",
            "043_paper_fill_cost_model_required.sql",
            "044_paper_portfolio_fill_environment_integrity.sql",
            "045_paper_portfolio_application_sequence_integrity.sql",
            "046_paper_portfolio_application_time_integrity.sql",
            "047_paper_fill_decision_time_integrity.sql",
            "048_paper_portfolio_initial_cash_integrity.sql",
            "049_paper_portfolio_cash_integrity.sql",
            "050_paper_portfolio_position_numeric_integrity.sql",
            "051_paper_portfolio_initial_cash_immutability.sql",
            "052_order_quantity_finite.sql",
            "053_fill_numeric_finite.sql",
            "054_paper_portfolio_pnl_numeric_integrity.sql",
            "055_market_bar_numeric_finite.sql",
            "056_experiment_definition_integrity.sql",
            "057_universe_member_valid_interval.sql",
            "058_paper_risk_assessments.sql",
            "059_paper_risk_assessment_immutability.sql",
            "060_paper_risk_effect_lineage.sql",
            "061_paper_risk_effect_type.sql",
            "062_paper_risk_reason_code_canonical.sql",
            "063_paper_risk_decision_time_integrity.sql",
            "064_paper_fill_cost_model_canonical.sql",
            "065_market_data_coverage_manifest.sql",
            "066_market_data_manifest_universe.sql",
            "067_market_bar_seal_lock.sql",
            "068_market_data_finalization_integrity.sql",
            "069_market_data_finalization_membership.sql",
            "070_market_data_manifest_unique_coverage.sql",
            "071_market_data_manifest_identity_continuity.sql",
            "072_market_data_manifest_identity_canonical.sql",
            "073_market_data_manifest_window_contract.sql",
            "074_market_data_manifest_structure.sql",
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
