-- New PAPER fills need two distinct times:
--   filled_at  = logical execution time supplied by the simulator/broker model
--   created_at = when PostgreSQL accepted the immutable fill into durable history
--
-- Do not fabricate persistence times for legacy fills. Existing rows remain NULL
-- so unknown history stays explicitly unknown. New rows receive a database default,
-- and PAPER inserts may not forge that value.
ALTER TABLE fills
    ADD COLUMN created_at TIMESTAMPTZ;

ALTER TABLE fills
    ALTER COLUMN created_at SET DEFAULT transaction_timestamp();

CREATE OR REPLACE FUNCTION hope_authenticate_paper_fill_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_environment TEXT;
BEGIN
    SELECT environment
      INTO source_environment
      FROM orders
     WHERE order_id = NEW.order_id;

    IF source_environment IS DISTINCT FROM 'PAPER' THEN
        RETURN NEW;
    END IF;

    IF NEW.created_at IS NULL THEN
        RAISE EXCEPTION 'PAPER_FILL_TIMESTAMP_REQUIRED'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_FILL_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_FILL_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_fill_database_timestamp
BEFORE INSERT ON fills
FOR EACH ROW
EXECUTE FUNCTION hope_authenticate_paper_fill_timestamp();
