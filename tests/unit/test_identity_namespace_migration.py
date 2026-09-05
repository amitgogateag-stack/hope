from pathlib import Path


def test_identity_namespace_migration_removes_overly_global_uniqueness() -> None:
    sql = (Path(__file__).parents[2] / "migrations" / "007_identity_namespace_integrity.sql").read_text()
    assert "DROP INDEX IF EXISTS uq_broker_active_instrument" in sql
    assert "ON broker_instruments (broker, instrument_id)" in sql
    assert "DROP INDEX IF EXISTS uq_identity_active_source_symbol" in sql
    assert "ON identity_mappings (broker_instrument_id, source_symbol)" in sql
