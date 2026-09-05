from pathlib import Path

ROOT = Path(__file__).parents[2]
MIGRATION = (ROOT / "migrations/006_cross_entity_trading_integrity.sql").read_text()


def test_order_signal_instrument_link_is_database_enforced():
    assert "hope_reject_order_signal_instrument_mismatch" in MIGRATION
    assert "ORDER_SIGNAL_INSTRUMENT_MISMATCH" in MIGRATION
    assert "BEFORE INSERT OR UPDATE OF signal_id, instrument_id ON orders" in MIGRATION


def test_position_signal_instrument_link_is_database_enforced():
    assert "hope_reject_position_signal_instrument_mismatch" in MIGRATION
    assert "POSITION_SIGNAL_INSTRUMENT_MISMATCH" in MIGRATION
    assert "BEFORE INSERT OR UPDATE OF opened_from_signal_id, instrument_id ON positions" in MIGRATION
