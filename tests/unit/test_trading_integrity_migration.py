from pathlib import Path

ROOT = Path(__file__).parents[2]
MIGRATION = (ROOT / "migrations/005_trading_integrity_guards.sql").read_text()
INITIAL_SCHEMA = (ROOT / "migrations/001_initial.sql").read_text()


def test_trading_integrity_migration_rejects_non_active_orders():
    assert "hope_reject_non_active_order_instrument" in MIGRATION
    assert "ORDER_INSTRUMENT_NOT_ACTIVE" in MIGRATION
    assert "BEFORE INSERT OR UPDATE OF instrument_id ON orders" in MIGRATION


def test_trading_integrity_migration_rejects_non_active_positions_and_missing_signal():
    assert "hope_reject_non_active_position_instrument" in MIGRATION
    assert "POSITION_MISSING_SIGNAL" in MIGRATION
    assert "POSITION_INSTRUMENT_NOT_ACTIVE" in MIGRATION


def test_initial_environment_constraint_has_no_live_mode():
    assert "environment IN ('RESEARCH','BACKTEST','WALK_FORWARD','PAPER')" in INITIAL_SCHEMA
