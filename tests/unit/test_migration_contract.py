from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_integrity_migration_enforces_duplicate_identity_guardrail():
    sql = (ROOT / "migrations/002_integrity_constraints.sql").read_text()
    assert "uq_identity_active_broker_instrument" in sql
    assert "WHERE status = 'ACTIVE'" in sql


def test_integrity_migration_prevents_historical_experiment_mutation():
    sql = (ROOT / "migrations/002_integrity_constraints.sql").read_text()
    assert "HOPE experiment history is immutable" in sql
    assert "BEFORE UPDATE OR DELETE ON experiments" in sql


def test_experiment_invalidation_is_append_only_event():
    sql = (ROOT / "migrations/003_experiment_invalidation_events.sql").read_text()
    assert "experiment_invalidations" in sql
    assert "REFERENCES experiments(experiment_id)" in sql


def test_market_data_finalization_requires_manifest_backed_exact_coverage():
    sql = (ROOT / "migrations/068_market_data_finalization_integrity.sql").read_text()
    assert "hope_guard_market_data_version_finalization" in sql
    assert "MARKET_DATA_FINALIZATION_REQUIRES_COVERAGE_MANIFEST" in sql
    assert "MARKET_DATA_FINALIZATION_COVERAGE_MISMATCH" in sql
    assert "MARKET_DATA_FINALIZATION_REQUIRES_PIT_UNIVERSE" in sql
    assert "BEFORE UPDATE ON dataset_versions" in sql
