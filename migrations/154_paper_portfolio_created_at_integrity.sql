-- PAPER portfolio creation time is accounting provenance.  Authenticate new
-- rows with PostgreSQL and prevent later rewrites while leaving mutable
-- materialized cash, version, and updated_at behavior unchanged.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_portfolios
         WHERE created_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_authenticate_paper_portfolio_created_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.created_at > clock_timestamp() THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_TIMESTAMP_IN_FUTURE'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.created_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_TIMESTAMP_NOT_DATABASE_AUTHENTICATED'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION hope_freeze_paper_portfolio_created_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_CREATED_AT_IMMUTABLE'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_created_at_database_timestamp
BEFORE INSERT ON paper_portfolios
FOR EACH ROW
EXECUTE FUNCTION hope_authenticate_paper_portfolio_created_at();

CREATE TRIGGER trg_paper_portfolio_created_at_immutable
BEFORE UPDATE OF created_at ON paper_portfolios
FOR EACH ROW
EXECUTE FUNCTION hope_freeze_paper_portfolio_created_at();
