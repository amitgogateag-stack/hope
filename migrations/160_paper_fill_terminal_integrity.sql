-- A durably terminal PAPER order must never accept a later fill.
CREATE OR REPLACE FUNCTION hope_guard_fill_against_paper_terminal()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
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

CREATE TRIGGER trg_fill_paper_terminal_integrity
BEFORE INSERT ON fills
FOR EACH ROW
EXECUTE FUNCTION hope_guard_fill_against_paper_terminal();
