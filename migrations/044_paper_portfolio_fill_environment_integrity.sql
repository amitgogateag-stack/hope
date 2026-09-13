-- PAPER portfolio accounting may consume fills only from PAPER orders.
CREATE OR REPLACE FUNCTION hope_require_paper_portfolio_fill_environment()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    fill_environment TEXT;
BEGIN
    SELECT o.environment INTO fill_environment
    FROM fills f
    JOIN orders o ON o.order_id = f.order_id
    WHERE f.fill_id = NEW.fill_id;

    IF fill_environment IS NOT NULL AND fill_environment <> 'PAPER' THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_FILL_ENVIRONMENT_MISMATCH'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_fill_environment_integrity
BEFORE INSERT ON paper_portfolio_fill_applications
FOR EACH ROW
EXECUTE FUNCTION hope_require_paper_portfolio_fill_environment();
