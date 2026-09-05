-- HOPE v0.1 trading-integrity guards.
-- Defense in depth: application invariants remain mandatory.

-- A terminal/duplicate/ambiguous/unresolved/superseded instrument must never receive an order.
CREATE OR REPLACE FUNCTION hope_reject_non_active_order_instrument()
RETURNS trigger AS $$
DECLARE instrument_status TEXT;
BEGIN
    SELECT status INTO instrument_status FROM instruments WHERE instrument_id = NEW.instrument_id;
    IF instrument_status IS DISTINCT FROM 'ACTIVE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat('ORDER_INSTRUMENT_NOT_ACTIVE: instrument ', NEW.instrument_id, ' has status ', instrument_status);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_orders_active_instrument ON orders;
CREATE TRIGGER trg_orders_active_instrument
BEFORE INSERT OR UPDATE OF instrument_id ON orders
FOR EACH ROW EXECUTE FUNCTION hope_reject_non_active_order_instrument();

-- Positions may only originate from a signal and an ACTIVE canonical instrument.
CREATE OR REPLACE FUNCTION hope_reject_non_active_position_instrument()
RETURNS trigger AS $$
DECLARE instrument_status TEXT;
BEGIN
    IF NEW.opened_from_signal_id IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat('POSITION_MISSING_SIGNAL: position ', NEW.position_id, ' has no source signal');
    END IF;
    SELECT status INTO instrument_status FROM instruments WHERE instrument_id = NEW.instrument_id;
    IF instrument_status IS DISTINCT FROM 'ACTIVE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat('POSITION_INSTRUMENT_NOT_ACTIVE: instrument ', NEW.instrument_id, ' has status ', instrument_status);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_positions_integrity ON positions;
CREATE TRIGGER trg_positions_integrity
BEFORE INSERT OR UPDATE OF instrument_id, opened_from_signal_id ON positions
FOR EACH ROW EXECUTE FUNCTION hope_reject_non_active_position_instrument();

-- The v0.1 environment CHECK constraint deliberately excludes LIVE.
-- P&L remains protected by its foreign key to positions.
