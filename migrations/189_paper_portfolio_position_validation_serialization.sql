-- Acquire the durable parent portfolio lock before every other position write
-- trigger.  UPDATE attempts must serialize on the existing owner even when a
-- malformed statement proposes a different portfolio identity.
CREATE OR REPLACE FUNCTION hope_lock_paper_portfolio_for_position_write()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    locked_portfolio_id UUID;
BEGIN
    locked_portfolio_id := CASE
        WHEN TG_OP = 'UPDATE' THEN OLD.portfolio_id
        ELSE NEW.portfolio_id
    END;

    PERFORM 1
      FROM paper_portfolios
     WHERE portfolio_id = locked_portfolio_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_POSITION_PARENT_NOT_FOUND'
            USING ERRCODE = '23503';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER trg_paper_portfolio_position_parent_lock
ON paper_portfolio_positions;

CREATE TRIGGER trg_aaa_paper_portfolio_position_parent_lock
BEFORE INSERT OR UPDATE ON paper_portfolio_positions
FOR EACH ROW
EXECUTE FUNCTION hope_lock_paper_portfolio_for_position_write();
