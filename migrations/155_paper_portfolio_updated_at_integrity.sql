-- PAPER portfolio updated_at is mutable projection provenance.  Callers may
-- change materialized state, but they must not choose the persistence timestamp.
-- PostgreSQL owns updated_at for both creation and every subsequent mutation.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM paper_portfolios
         WHERE updated_at < created_at
            OR updated_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_UPDATED_AT_INVALID'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION hope_manage_paper_portfolio_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.updated_at IS DISTINCT FROM transaction_timestamp() THEN
            RAISE EXCEPTION 'PAPER_PORTFOLIO_UPDATED_AT_NOT_DATABASE_AUTHENTICATED'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.updated_at IS DISTINCT FROM OLD.updated_at THEN
        RAISE EXCEPTION 'PAPER_PORTFOLIO_UPDATED_AT_CALLER_FORBIDDEN'
            USING ERRCODE = '23514';
    END IF;

    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_portfolio_updated_at_database_managed
BEFORE INSERT OR UPDATE ON paper_portfolios
FOR EACH ROW
EXECUTE FUNCTION hope_manage_paper_portfolio_updated_at();
