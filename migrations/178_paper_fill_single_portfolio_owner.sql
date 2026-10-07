-- A durable PAPER fill cannot be booked into multiple portfolios.
-- Refuse historical duplicates instead of silently normalizing accounting.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM paper_portfolio_fill_applications
        GROUP BY fill_id HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'PAPER_PORTFOLIO_FILL_MULTIPLE_PORTFOLIOS';
    END IF;
END;
$$;

-- Unique ownership remains authoritative even under concurrent portfolio writers.
ALTER TABLE paper_portfolio_fill_applications
    ADD CONSTRAINT uq_paper_portfolio_fill_single_owner UNIQUE (fill_id);
