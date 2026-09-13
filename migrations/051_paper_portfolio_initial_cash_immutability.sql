-- PAPER portfolio initial capital is an accounting provenance baseline and must not be rewritten.
CREATE OR REPLACE FUNCTION hope_enforce_paper_portfolio_initial_cash_immutability()
RETURNS trigger AS $$
BEGIN
    IF NEW.initial_cash IS DISTINCT FROM OLD.initial_cash THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'PAPER_PORTFOLIO_INITIAL_CASH_IMMUTABLE';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_paper_portfolio_initial_cash_immutability
BEFORE UPDATE OF initial_cash ON paper_portfolios
FOR EACH ROW EXECUTE FUNCTION hope_enforce_paper_portfolio_initial_cash_immutability();
