-- Serialize PAPER fill and terminal writes through their immutable source order.
CREATE OR REPLACE FUNCTION hope_guard_fill_against_paper_terminal()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM 1
    FROM orders
    WHERE order_id = NEW.order_id
    FOR UPDATE;

    PERFORM 1
    FROM paper_order_terminal_events
    WHERE order_id = NEW.order_id;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'PAPER_FILL_AFTER_TERMINAL_FORBIDDEN';
    END IF;

    RETURN NEW;
END;
$$;
