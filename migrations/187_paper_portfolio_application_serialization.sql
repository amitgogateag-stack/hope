-- Every PAPER fill-application attempt must enter the same parent-portfolio
-- critical section as authoritative recovery before any other application
-- trigger reads or validates durable accounting evidence.  The sequence guard
-- already acquired this lock, but PostgreSQL runs same-kind triggers by name,
-- so the database-timestamp guard ran before that serialization boundary.
CREATE OR REPLACE FUNCTION hope_lock_paper_portfolio_for_application_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1
      FROM paper_portfolios
     WHERE portfolio_id = NEW.portfolio_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_APPLICATION_PARENT_NOT_FOUND'
            USING ERRCODE = '23503';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_aaa_paper_portfolio_application_parent_lock
BEFORE INSERT ON paper_portfolio_fill_applications
FOR EACH ROW
EXECUTE FUNCTION hope_lock_paper_portfolio_for_application_insert();

-- The dedicated first trigger now owns serialization.  Keep the sequence guard
-- focused on deriving the next sequence while that lock is already held.
CREATE OR REPLACE FUNCTION hope_enforce_paper_portfolio_application_sequence()
RETURNS trigger AS $$
DECLARE
    applied_count BIGINT;
BEGIN
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
