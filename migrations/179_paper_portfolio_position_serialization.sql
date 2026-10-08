-- Materialized PAPER positions must share the parent portfolio lock used by
-- authoritative recovery.  This prevents a direct position writer from
-- changing the projection while recovery verifies its replayed history.
CREATE OR REPLACE FUNCTION hope_lock_paper_portfolio_for_position_write()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1
      FROM paper_portfolios
     WHERE portfolio_id = NEW.portfolio_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_POSITION_PARENT_NOT_FOUND'
            USING ERRCODE = '23503';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_position_parent_lock
BEFORE INSERT OR UPDATE ON paper_portfolio_positions
FOR EACH ROW
EXECUTE FUNCTION hope_lock_paper_portfolio_for_position_write();
