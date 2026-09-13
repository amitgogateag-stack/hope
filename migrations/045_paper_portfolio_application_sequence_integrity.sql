-- PAPER portfolio application history must remain contiguous for deterministic restart reconstruction.
CREATE OR REPLACE FUNCTION hope_enforce_paper_portfolio_application_sequence()
RETURNS trigger AS $$
DECLARE
    applied_count BIGINT;
BEGIN
    -- Serialize application writers for one portfolio before deriving the next sequence.
    PERFORM 1
    FROM paper_portfolios
    WHERE portfolio_id = NEW.portfolio_id
    FOR UPDATE;

    SELECT count(*) INTO applied_count
    FROM paper_portfolio_fill_applications
    WHERE portfolio_id = NEW.portfolio_id;

    IF NEW.application_sequence <> applied_count + 1 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = concat(
                'PAPER_PORTFOLIO_APPLICATION_SEQUENCE_GAP: expected ',
                applied_count + 1,
                ', got ',
                NEW.application_sequence
            );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_paper_portfolio_application_sequence_integrity
BEFORE INSERT ON paper_portfolio_fill_applications
FOR EACH ROW EXECUTE FUNCTION hope_enforce_paper_portfolio_application_sequence();
