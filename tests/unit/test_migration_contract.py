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


def test_market_data_finalization_requires_valid_pit_universe_membership():
    sql = (ROOT / "migrations/069_market_data_finalization_membership.sql").read_text()
    assert "hope_guard_market_data_version_finalization" in sql
    assert "um.instrument_id = e.instrument_id" in sql
    assert "e.event_time >= um.valid_from" in sql
    assert "e.event_time < um.valid_to" in sql
    assert "MARKET_DATA_FINALIZATION_UNIVERSE_MEMBERSHIP_MISMATCH" in sql


def test_market_data_finalization_rejects_duplicate_manifest_coverage():
    sql = (ROOT / "migrations/070_market_data_manifest_unique_coverage.sql").read_text()
    assert "count(DISTINCT (instrument_id, event_time))" in sql
    assert "MARKET_DATA_FINALIZATION_DUPLICATE_MANIFEST_COVERAGE" in sql
    assert "BEFORE UPDATE ON dataset_versions" in sql


def test_market_data_finalization_preserves_provider_identity_continuity():
    sql = (
        ROOT / "migrations/071_market_data_manifest_identity_continuity.sql"
    ).read_text()
    assert "GROUP BY source, source_symbol" in sql
    assert "count(DISTINCT instrument_id) > 1" in sql
    assert "MARKET_DATA_FINALIZATION_IDENTITY_BINDING_CONFLICT" in sql
    assert "BEFORE UPDATE ON dataset_versions" in sql


def test_market_data_finalization_requires_canonical_provider_identity():
    sql = (
        ROOT / "migrations/072_market_data_manifest_identity_canonical.sql"
    ).read_text()
    assert "w->>'source' IS NULL" in sql
    assert "w->>'source' <> dataset_source" in sql
    assert "i->>'source_symbol' <> btrim(i->>'source_symbol')" in sql
    assert "MARKET_DATA_FINALIZATION_IDENTITY_NOT_CANONICAL" in sql


def test_market_data_finalization_preserves_request_window_contract():
    sql = (
        ROOT / "migrations/073_market_data_manifest_window_contract.sql"
    ).read_text()
    assert "interval_seconds')::integer <= 0" in sql
    assert "::timestamptz <=" in sql
    assert "EXTRACT(EPOCH FROM" in sql
    assert "[+-][0-9]{2}:[0-9]{2}" in sql
    assert "MARKET_DATA_FINALIZATION_WINDOW_CONTRACT_INVALID" in sql
    assert "BEFORE UPDATE ON dataset_versions" in sql
