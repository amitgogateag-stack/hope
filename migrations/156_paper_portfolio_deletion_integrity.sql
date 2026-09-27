-- PAPER portfolio rows are durable restart projections.  The authoritative
-- repository updates them in place and never deletes them; removal would erase
-- accounting state or bypass replay verification.
CREATE OR REPLACE FUNCTION hope_forbid_paper_portfolio_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'PAPER_PORTFOLIO_DELETE_FORBIDDEN'
        USING ERRCODE = '23514';
END;
$$;

CREATE OR REPLACE FUNCTION hope_forbid_paper_portfolio_position_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'PAPER_PORTFOLIO_POSITION_DELETE_FORBIDDEN'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER trg_paper_portfolio_delete_forbidden
BEFORE DELETE ON paper_portfolios
FOR EACH ROW
EXECUTE FUNCTION hope_forbid_paper_portfolio_delete();

CREATE TRIGGER trg_paper_portfolio_position_delete_forbidden
BEFORE DELETE ON paper_portfolio_positions
FOR EACH ROW
EXECUTE FUNCTION hope_forbid_paper_portfolio_position_delete();
