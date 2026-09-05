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
