-- Authoritative PAPER P&L instrument must match the instrument of its applied fill's order.
CREATE OR REPLACE FUNCTION hope_guard_paper_portfolio_pnl_instrument()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    fill_instrument UUID;
BEGIN
    SELECT o.instrument_id INTO fill_instrument
    FROM fills f
    JOIN orders o ON o.order_id = f.order_id
    WHERE f.fill_id = NEW.fill_id;

    IF fill_instrument IS DISTINCT FROM NEW.instrument_id THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_INSTRUMENT_MISMATCH: fill % instrument % vs pnl instrument %',
            NEW.fill_id, fill_instrument, NEW.instrument_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_pnl_instrument_integrity
BEFORE INSERT ON paper_portfolio_pnl_events
FOR EACH ROW
EXECUTE FUNCTION hope_guard_paper_portfolio_pnl_instrument();
