-- PAPER accounting cannot be applied before the source fill exists.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM paper_portfolio_fill_applications a
        JOIN fills f ON f.fill_id = a.fill_id
        WHERE a.applied_at < f.filled_at
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'PAPER_PORTFOLIO_APPLICATION_PRECEDES_FILL';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_enforce_paper_portfolio_application_time()
RETURNS trigger AS $$
DECLARE
    previous_fill_time TIMESTAMPTZ;
    new_fill_time TIMESTAMPTZ;
BEGIN
    SELECT filled_at INTO new_fill_time
    FROM fills
    WHERE fill_id = NEW.fill_id;

    IF NEW.applied_at < new_fill_time THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'PAPER_PORTFOLIO_APPLICATION_PRECEDES_FILL';
    END IF;

    IF NEW.application_sequence > 1 THEN
        SELECT f.filled_at INTO previous_fill_time
        FROM paper_portfolio_fill_applications a
        JOIN fills f ON f.fill_id = a.fill_id
        WHERE a.portfolio_id = NEW.portfolio_id
          AND a.application_sequence = NEW.application_sequence - 1;

        IF previous_fill_time IS NULL THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'PAPER_PORTFOLIO_APPLICATION_PREVIOUS_FILL_MISSING';
        END IF;

        IF new_fill_time < previous_fill_time THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'PAPER_PORTFOLIO_APPLICATION_TIME_REGRESSION';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
