-- HOPE v0.1 cross-entity trading integrity.
-- A signal, order and resulting position must refer to the same canonical instrument.

CREATE OR REPLACE FUNCTION hope_reject_order_signal_instrument_mismatch()
RETURNS trigger AS $$
DECLARE signal_instrument UUID;
BEGIN
    SELECT instrument_id INTO signal_instrument
    FROM signals WHERE signal_id = NEW.signal_id;
    IF signal_instrument IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat('ORDER_SIGNAL_NOT_FOUND: signal ', NEW.signal_id);
    END IF;
    IF signal_instrument IS DISTINCT FROM NEW.instrument_id THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat('ORDER_SIGNAL_INSTRUMENT_MISMATCH: signal ', NEW.signal_id,
                ' instrument ', signal_instrument, ' vs order instrument ', NEW.instrument_id);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_orders_signal_instrument ON orders;
CREATE TRIGGER trg_orders_signal_instrument
BEFORE INSERT OR UPDATE OF signal_id, instrument_id ON orders
FOR EACH ROW EXECUTE FUNCTION hope_reject_order_signal_instrument_mismatch();

CREATE OR REPLACE FUNCTION hope_reject_position_signal_instrument_mismatch()
RETURNS trigger AS $$
DECLARE signal_instrument UUID;
BEGIN
    SELECT instrument_id INTO signal_instrument
    FROM signals WHERE signal_id = NEW.opened_from_signal_id;
    IF signal_instrument IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat('POSITION_SIGNAL_NOT_FOUND: signal ', NEW.opened_from_signal_id);
    END IF;
    IF signal_instrument IS DISTINCT FROM NEW.instrument_id THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat('POSITION_SIGNAL_INSTRUMENT_MISMATCH: signal ', NEW.opened_from_signal_id,
                ' instrument ', signal_instrument, ' vs position instrument ', NEW.instrument_id);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_positions_signal_instrument ON positions;
CREATE TRIGGER trg_positions_signal_instrument
BEFORE INSERT OR UPDATE OF opened_from_signal_id, instrument_id ON positions
FOR EACH ROW EXECUTE FUNCTION hope_reject_position_signal_instrument_mismatch();

-- P&L must remain traceable to an existing position. The FK in 001 enforces existence;
-- this migration documents that relationship as an explicit invariant for test discovery.
