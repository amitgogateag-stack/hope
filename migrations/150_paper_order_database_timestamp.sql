-- Persisted PAPER orders are immutable execution intent.  Authenticate
-- created_at in PostgreSQL so direct SQL cannot forge when that intent entered
-- durable history.  Non-PAPER environments retain their existing behavior.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM orders
         WHERE environment = 'PAPER'
           AND created_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_ORDER_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_authenticate_paper_order_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.environment <> 'PAPER' THEN
        RETURN NEW;
    END IF;

    IF NEW.created_at > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_ORDER_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_ORDER_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_order_database_timestamp
BEFORE INSERT ON orders
FOR EACH ROW
EXECUTE FUNCTION hope_authenticate_paper_order_timestamp();
