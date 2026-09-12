-- Durable fills must never cumulatively exceed their source order quantity.
CREATE OR REPLACE FUNCTION hope_guard_fill_cumulative_quantity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    source_order_quantity NUMERIC;
    already_filled NUMERIC;
BEGIN
    SELECT quantity INTO source_order_quantity
    FROM orders
    WHERE order_id = NEW.order_id
    FOR UPDATE;

    IF source_order_quantity IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT COALESCE(SUM(quantity), 0) INTO already_filled
    FROM fills
    WHERE order_id = NEW.order_id;

    IF already_filled + NEW.quantity > source_order_quantity THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'FILL_CUMULATIVE_QUANTITY_EXCEEDS_ORDER';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_fill_cumulative_quantity_integrity
BEFORE INSERT ON fills
FOR EACH ROW
EXECUTE FUNCTION hope_guard_fill_cumulative_quantity();
