-- Durable PAPER P&L evidence must share the parent portfolio lock used by
-- authoritative recovery.  This prevents an out-of-band P&L writer from
-- introducing a row while recovery verifies the immutable accounting history.
CREATE OR REPLACE FUNCTION hope_lock_paper_portfolio_for_pnl_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1
      FROM paper_portfolios
     WHERE portfolio_id = NEW.portfolio_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_PNL_PARENT_NOT_FOUND'
            USING ERRCODE = '23503';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_pnl_parent_lock
BEFORE INSERT ON paper_portfolio_pnl_events
FOR EACH ROW
EXECUTE FUNCTION hope_lock_paper_portfolio_for_pnl_insert();
